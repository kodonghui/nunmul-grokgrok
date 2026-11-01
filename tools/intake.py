"""Validate Grok voice/text intents, preview, then commit a confirmed draft."""

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

if __package__:
    from .common import JsonArgumentParser, SEOUL, ToolError, iso, parse_instant, read_json, run_cli
    from .dose_rules import VOICE_PATH
    from .notification_images import attach_mark, family_mark_rows, load_catalog, taken_ids
    from . import family_store as family
    from . import med_store as meds
else:
    from common import JsonArgumentParser, SEOUL, ToolError, iso, parse_instant, read_json, run_cli
    from dose_rules import VOICE_PATH
    from notification_images import attach_mark, family_mark_rows, load_catalog, taken_ids
    import family_store as family
    import med_store as meds


def _keys(payload, allowed, required):
    if not isinstance(payload, dict) or set(payload) - set(allowed) or not set(required).issubset(payload):
        raise ToolError("INVALID_INPUT", "허용된 구조화 명령 필드를 확인해 주세요.")


def apply_intent(store, profile_id, action, payload, at, request_id):
    if isinstance(payload, dict):
        for field in ("medicineId", "appointmentId"):
            if field in payload and (not isinstance(payload[field], str) or not payload[field]):
                raise ToolError("INVALID_INPUT", "대상 ID를 확인해 주세요.")
    if action == "medicine.add":
        allowed = meds.PATCH_FIELDS | {"items", "confirmedAt"}
        _keys(payload, allowed, {"items"})
        if not isinstance(payload["items"], list):
            raise ToolError("INVALID_INPUT", "약 항목 목록이 필요합니다.")
        confirmed = deepcopy(payload)
        confirmed["confirmedAt"] = iso(at)
        for item in confirmed["items"]:
            _keys(item, meds.PATCH_FIELDS | {"name", "itemSeq", "confirmedAt", "readNo"}, {"name"})
            item["confirmedAt"] = iso(at)
        return family.medicine_action(store, profile_id, "add", at, confirmed, request_id=request_id)
    if action == "medicine.schedule":
        _keys(payload, {"medicineId", "schedule"}, {"medicineId", "schedule"})
        return family.medicine_action(store, profile_id, "schedule", at, payload["schedule"], payload["medicineId"])
    if action in ("medicine.stop", "medicine.clear", "medicine.delete"):
        _keys(payload, {"medicineId"}, {"medicineId"})
        return family.medicine_action(store, profile_id, action.split(".")[1], at, medicine_id=payload["medicineId"])
    if action in ("dose.mark", "dose.skip"):
        _keys(payload, {"key", "medicineIds"}, set())
        ids = payload.get("medicineIds")
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(value, str) for value in ids)):
            raise ToolError("INVALID_INPUT", "약 ID 목록을 확인해 주세요.")
        return family.mark(store, profile_id, at, "chat", payload.get("key"), ids, action == "dose.skip", read_json(VOICE_PATH))
    if action == "appointment.add":
        return family.appointment_action(store, profile_id, "add", at, payload)
    if action == "appointment.update":
        _keys(payload, {"appointmentId", "changes"}, {"appointmentId", "changes"})
        return family.appointment_action(store, profile_id, "update", at, payload["changes"], payload["appointmentId"])
    if action == "appointment.cancel":
        _keys(payload, {"appointmentId"}, {"appointmentId"})
        return family.appointment_action(store, profile_id, "cancel", at, appointment_id=payload["appointmentId"])
    raise ToolError("INVALID_ACTION", "지원하지 않는 등록·관리 명령입니다.")


def propose(store, request, at, reference=None, replace_id=None):
    _keys(request, {"action", "profileId", "source", "requestId", "payload"}, {"action", "source", "requestId", "payload"})
    profile = family.resolve_profile(store, reference if reference is not None else request.get("profileId"))
    if request.get("profileId") is not None and family.resolve_profile(store, request["profileId"])["id"] != profile["id"]:
        raise ToolError("PROFILE_MISMATCH", "초안 대상 프로필이 다릅니다.")
    if request["source"] not in ("chat", "voice"):
        raise ToolError("INVALID_INPUT", "source는 chat 또는 voice입니다.")
    request_id = request["requestId"]
    if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 200:
        raise ToolError("INVALID_INPUT", "등록 요청 ID가 필요합니다.")
    digest = meds._hash({"profileId": profile["id"], "action": request["action"], "payload": request["payload"]})
    ledger_key = profile["id"] + "|" + request_id
    previous = store["intakeRequests"].get(ledger_key)
    if previous is not None:
        if previous["digest"] != digest:
            raise ToolError("REQUEST_CONFLICT", "같은 요청 ID의 내용이 다릅니다.")
        draft = store["drafts"][previous["draftId"]]
        return {"draftId": draft["id"], "profileId": profile["id"], "state": draft["state"], "replayed": True,
                "expectedRevision": draft["expectedRevision"], "expiresAt": draft["expiresAt"]}
    preview_store = deepcopy(store)
    apply_intent(preview_store, profile["id"], request["action"], request["payload"], at, "intake-preview:" + request_id)
    family._refresh(preview_store, at)
    preview = family.schedule(preview_store, profile["id"])
    if replace_id is not None:
        previous_draft = store["drafts"].get(replace_id)
        if previous_draft is None or previous_draft["state"] != "PROPOSED":
            raise ToolError("DRAFT_NOT_PENDING", "교체할 대기 초안이 없습니다.")
        if previous_draft["profileId"] != profile["id"]:
            raise ToolError("PROFILE_MISMATCH", "다른 프로필의 초안은 교체할 수 없습니다.")
        previous_draft["state"] = "SUPERSEDED"
    draft_id = "draft_" + uuid4().hex
    draft = {"id": draft_id, "profileId": profile["id"], "action": request["action"], "payload": deepcopy(request["payload"]),
             "source": request["source"], "requestId": request_id, "digest": digest, "expectedRevision": store["revision"] + 1,
             "expiresAt": iso(at + timedelta(minutes=15)), "state": "PROPOSED", "result": None}
    store["drafts"][draft_id] = draft
    store["intakeRequests"][ledger_key] = {"draftId": draft_id, "digest": digest}
    return {"draftId": draft_id, "profileId": profile["id"], "profileAlias": profile["alias"], "state": draft["state"],
            "expectedRevision": draft["expectedRevision"], "expiresAt": draft["expiresAt"], "replayed": False,
            "preview": {"action": draft["action"], "calendarEvents": preview["calendarEvents"]}}


def confirm(store, draft_id, at, reference=None, accept=False):
    draft = store["drafts"].get(draft_id)
    if draft is None:
        raise ToolError("DRAFT_NOT_FOUND", "등록 초안을 찾을 수 없습니다.")
    profile = family.resolve_profile(store, reference)
    if profile["id"] != draft["profileId"]:
        raise ToolError("PROFILE_MISMATCH", "초안 대상 프로필이 다릅니다.")
    if not accept:
        raise ToolError("CONFIRMATION_REQUIRED", "사용자의 실제 동의 또는 명확한 실행 지시가 필요합니다.")
    if draft["state"] == "COMMITTED":
        return {**deepcopy(draft["result"]), "draftId": draft_id, "replayed": True}
    if draft["state"] != "PROPOSED":
        raise ToolError("DRAFT_NOT_PENDING", "취소되거나 교체된 초안입니다.")
    if at >= parse_instant(draft["expiresAt"]):
        raise ToolError("DRAFT_EXPIRED", "초안이 만료됐습니다. 다시 확인해 주세요.")
    if draft["expectedRevision"] != store["revision"]:
        raise ToolError("REVISION_CONFLICT", "목록이 바뀌었습니다. 새 초안을 확인해 주세요.")
    result = apply_intent(store, profile["id"], draft["action"], draft["payload"], at, "intake:" + draft_id)
    draft.update(state="COMMITTED", result=deepcopy(result))
    return {**result, "draftId": draft_id, "replayed": False}


def cancel(store, draft_id, reference=None):
    draft = store["drafts"].get(draft_id)
    if draft is None:
        raise ToolError("DRAFT_NOT_FOUND", "등록 초안을 찾을 수 없습니다.")
    profile = family.resolve_profile(store, reference)
    if profile["id"] != draft["profileId"]:
        raise ToolError("PROFILE_MISMATCH", "초안 대상 프로필이 다릅니다.")
    if draft["state"] == "COMMITTED":
        raise ToolError("DRAFT_ALREADY_COMMITTED", "이미 저장했습니다. 해당 약·캘린더 일정의 변경 명령을 사용해 주세요.")
    if draft["state"] == "PROPOSED":
        draft["state"] = "CANCELLED"
    return {"draftId": draft_id, "profileId": profile["id"], "state": draft["state"]}


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("propose", "confirm", "cancel", "appointments"):
        sub = commands.add_parser(command)
        sub.add_argument("--store", type=Path, default=family.DEFAULT_STORE)
        sub.add_argument("--profile")
        sub.add_argument("--at")
        if command == "propose":
            sub.add_argument("--in", dest="input", type=Path, required=True)
            sub.add_argument("--replace")
        elif command != "appointments":
            sub.add_argument("draft_id")
        if command == "confirm":
            sub.add_argument("--accept", action="store_true")
            sub.add_argument("--with-images", action="store_true", help="복용 확인 최초 성공에 미소 이미지 첨부 정보를 반환")
    args = parser.parse_args(argv)
    at = parse_instant(args.at) if args.at else datetime.now(SEOUL)
    catalog = load_catalog() if getattr(args, "with_images", False) else None
    def action(store):
        if args.command == "appointments":
            return family.appointment_action(store, args.profile, "list", at)
        if args.command == "propose":
            return propose(store, read_json(args.input), at, args.profile, args.replace)
        if args.command == "confirm":
            draft = store["drafts"].get(args.draft_id)
            dose_mark = catalog is not None and draft is not None and draft["action"] == "dose.mark"
            previous = taken_ids(family.resolve_profile(store, args.profile)["store"]["doses"]) if dose_mark else None
            result = confirm(store, args.draft_id, at, args.profile, args.accept)
            return attach_mark(result, previous, family_mark_rows(store, result), catalog) if dose_mark else result
        return cancel(store, args.draft_id, args.profile)
    return family.transact(args.store, action, at, include_changes=args.command == "confirm")


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
