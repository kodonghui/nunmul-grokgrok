"""One account, isolated family stores, drafts and calendar identities."""

from copy import deepcopy
from datetime import datetime, time, timedelta
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

if __package__:
    from .common import SEOUL, ToolError, atomic_write_json, iso, parse_date, parse_hhmm, parse_instant, read_json, state_lock
    from .dose_rules import is_quiet
    from . import med_store as meds
else:
    from common import SEOUL, ToolError, atomic_write_json, iso, parse_date, parse_hhmm, parse_instant, read_json, state_lock
    from dose_rules import is_quiet
    import med_store as meds


DEFAULT_STORE = meds.DEFAULT_STORE


def _name(value, field="alias", limit=30):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or any(ord(c) < 32 for c in value):
        raise ToolError("INVALID_INPUT", f"{field}: 1~{limit}자의 문구가 필요합니다.")
    return value.strip()


def _clinical(value, at):
    if not isinstance(value, dict) or set(value) - {"ageYears", "ageMonths", "pregnant", "confirmedOn"}:
        raise ToolError("INVALID_INPUT", "선택적 나이·임신 조건을 확인해 주세요.")
    result = {"ageYears": None, "ageMonths": None, "pregnant": None, "confirmedOn": at.date().isoformat()}
    result.update(value)
    for key, maximum in (("ageYears", 130), ("ageMonths", 1560)):
        if result[key] is not None and (type(result[key]) is not int or not 0 <= result[key] <= maximum):
            raise ToolError("INVALID_INPUT", "유효한 현재 나이가 필요합니다.")
    if result["ageYears"] is not None and result["ageMonths"] is not None:
        raise ToolError("INVALID_INPUT", "나이는 세 또는 개월 중 하나로 지정해 주세요.")
    if result["pregnant"] is not None and type(result["pregnant"]) is not bool:
        raise ToolError("INVALID_INPUT", "pregnant는 true/false/null입니다.")
    parse_date(result["confirmedOn"], "clinical.confirmedOn")
    return result


def _profile(profile_id, alias, at, store=None):
    return {"id": profile_id, "alias": alias, "relationship": "SELF" if alias == "나" else "FAMILY",
            "createdAt": iso(at), "clinical": _clinical({}, at),
            "store": store if store is not None else meds.empty_store(), "appointments": {},
            "calendarAcks": {}, "legacyKeys": []}


def empty_family(at):
    profile = _profile("profile_self", "나", at)
    return {"schemaVersion": 3, "revision": 0, "profiles": {profile["id"]: profile},
            "drafts": {}, "intakeRequests": {}, "migration": None}


def validate_family(family):
    if (not isinstance(family, dict) or family.get("schemaVersion") != 3
            or type(family.get("revision")) is not int or family["revision"] < 0
            or any(not isinstance(family.get(k), dict) for k in ("profiles", "drafts", "intakeRequests"))
            or not family["profiles"]):
        raise ToolError("INVALID_STORE", "가족 원장의 형식과 버전을 확인해 주세요.")
    aliases = set()
    for profile_id, profile in family["profiles"].items():
        required = {"id", "alias", "relationship", "createdAt", "clinical", "store", "appointments", "calendarAcks", "legacyKeys"}
        if (not isinstance(profile, dict) or not required.issubset(profile) or profile["id"] != profile_id
                or not isinstance(profile_id, str) or not re.fullmatch(r"profile_[a-z0-9_]+", profile_id)
                or not isinstance(profile["appointments"], dict) or not isinstance(profile["calendarAcks"], dict)
                or not isinstance(profile["legacyKeys"], list)
                or any(not isinstance(key, str) for key in profile["legacyKeys"])):
            raise ToolError("INVALID_STORE", "저장된 프로필을 확인해 주세요.")
        alias = _name(profile["alias"])
        normalized = unicodedata.normalize("NFKC", alias).casefold()
        if normalized in aliases:
            raise ToolError("INVALID_STORE", "중복된 프로필 별칭이 있습니다.")
        aliases.add(normalized)
        _name(profile["relationship"], "relationship", 20)
        at = parse_instant(profile["createdAt"])
        _clinical(profile["clinical"], at)
        meds.validate_store(profile["store"])
        for appointment_id, appointment in profile["appointments"].items():
            if (not isinstance(appointment, dict) or appointment.get("id") != appointment_id
                    or not isinstance(appointment_id, str) or not re.fullmatch(r"appt_[0-9a-f]{32}", appointment_id)
                    or not {"title", "confirmedAt", "cancelledAt", "history"}.issubset(appointment)
                    or not isinstance(appointment["history"], list)):
                raise ToolError("INVALID_STORE", "저장된 캘린더 일정을 확인해 주세요.")
            _appointment_fields(appointment)
            parse_instant(appointment["confirmedAt"])
            if appointment["cancelledAt"] is not None:
                parse_instant(appointment["cancelledAt"])
            if not isinstance(appointment.get("sentReminders", {}), dict):
                raise ToolError("INVALID_STORE", "일정 알림 기록을 확인해 주세요.")
    for draft_id, draft in family["drafts"].items():
        required = {"id", "profileId", "action", "payload", "source", "requestId", "digest", "expectedRevision", "expiresAt", "state", "result"}
        if (not isinstance(draft, dict) or not required.issubset(draft) or draft["id"] != draft_id
                or not isinstance(draft["profileId"], str) or draft["profileId"] not in family["profiles"] or not isinstance(draft["payload"], dict)
                or draft["source"] not in ("voice", "chat") or type(draft["expectedRevision"]) is not int
                or draft["state"] not in ("PROPOSED", "COMMITTED", "CANCELLED", "SUPERSEDED")):
            raise ToolError("INVALID_STORE", "저장된 등록 초안을 확인해 주세요.")
        parse_instant(draft["expiresAt"])
    for request in family["intakeRequests"].values():
        if (not isinstance(request, dict) or not isinstance(request.get("draftId"), str)
                or request["draftId"] not in family["drafts"] or not isinstance(request.get("digest"), str)):
            raise ToolError("INVALID_STORE", "초안 요청 기록을 확인해 주세요.")
    return family


def resolve_profile(family, reference=None):
    if reference is None:
        if len(family["profiles"]) != 1:
            raise ToolError("PROFILE_REQUIRED", "대상 가족 프로필을 지정해 주세요.")
        return next(iter(family["profiles"].values()))
    if not isinstance(reference, str):
        raise ToolError("INVALID_INPUT", "프로필 ID 또는 정확한 별칭이 필요합니다.")
    if reference in family["profiles"]:
        return family["profiles"][reference]
    matches = [p for p in family["profiles"].values() if p["alias"] == reference]
    if len(matches) != 1:
        raise ToolError("PROFILE_NOT_FOUND", "해당 프로필을 찾을 수 없습니다.")
    return matches[0]


def create_profile(family, alias, at, relationship="FAMILY", clinical=None):
    alias = _name(alias)
    normalized = unicodedata.normalize("NFKC", alias).casefold()
    if any(unicodedata.normalize("NFKC", p["alias"]).casefold() == normalized for p in family["profiles"].values()):
        raise ToolError("DUPLICATE_PROFILE", "이미 있는 프로필 별칭입니다.")
    profile = _profile("profile_" + uuid4().hex, alias, at)
    profile["relationship"] = _name(relationship, "relationship", 20)
    if clinical is not None:
        profile["clinical"] = _clinical(clinical, at)
    family["profiles"][profile["id"]] = profile
    return {"profileId": profile["id"], "profileAlias": alias}


def update_profile(family, reference, patch, at):
    profile = resolve_profile(family, reference)
    if not isinstance(patch, dict) or not patch or set(patch) - {"alias", "relationship", "clinical"}:
        raise ToolError("INVALID_INPUT", "프로필 변경 필드를 확인해 주세요.")
    if "alias" in patch:
        alias = _name(patch["alias"])
        normalized = unicodedata.normalize("NFKC", alias).casefold()
        if any(p["id"] != profile["id"] and unicodedata.normalize("NFKC", p["alias"]).casefold() == normalized
               for p in family["profiles"].values()):
            raise ToolError("DUPLICATE_PROFILE", "이미 있는 프로필 별칭입니다.")
        if profile["alias"] != alias:
            profile["alias"] = alias
            for applied in profile["store"]["calendar"]["applied"].values():
                applied["contentHash"] = "profile-alias-changed"
    if "relationship" in patch:
        profile["relationship"] = _name(patch["relationship"], "relationship", 20)
    if "clinical" in patch:
        profile["clinical"] = _clinical(patch["clinical"], at)
    return {"profileId": profile["id"], "profileAlias": profile["alias"]}


def list_profiles(family):
    return {"profiles": [{k: deepcopy(p[k]) for k in ("id", "alias", "relationship", "clinical", "createdAt")}
                         for p in family["profiles"].values()]}


def _result(profile, result):
    return {**result, "profileId": profile["id"], "profileAlias": profile["alias"]}


def medicine_action(family, reference, operation, at, payload=None, medicine_id=None, request_id=None, slots=None):
    profile = resolve_profile(family, reference)
    if operation not in ("list", "add") and (not isinstance(medicine_id, str) or not medicine_id):
        raise ToolError("INVALID_INPUT", "대상 약 ID를 확인해 주세요.")
    store = profile["store"]
    if operation == "list":
        result = meds.list_medicines(store, at, include_deleted=bool(payload and payload.get("all")))
    elif operation == "add":
        result = meds.add_medicines(store, payload, at, request_id, slots)
    elif operation == "schedule":
        result = meds.set_schedule(store, medicine_id, payload, at, slots)
    else:
        operation_fn = {"stop": meds.stop_medicine, "clear": meds.clear_schedule, "delete": meds.delete_medicine}.get(operation)
        if operation_fn is None:
            raise ToolError("INVALID_INPUT", "지원하지 않는 약 관리 명령입니다.")
        result = operation_fn(store, medicine_id, at)
    return _result(profile, result)


def scoped_key(profile, key):
    return profile["id"] + "|" + key


def _inner_key(profile, key):
    if key is None:
        return None
    if not isinstance(key, str) or not key:
        raise ToolError("INVALID_INPUT", "회차 key를 확인해 주세요.")
    if key.startswith("profile_"):
        prefix, separator, rest = key.partition("|")
        if prefix != profile["id"] or not separator:
            raise ToolError("PROFILE_MISMATCH", "다른 프로필의 회차입니다.")
        return rest
    return key


def mark(family, reference, at, source=None, key=None, medicine_ids=None, skip=False, voice=None):
    profile = resolve_profile(family, reference)
    result = meds.store_mark(profile["store"], at, source, _inner_key(profile, key), medicine_ids, skip, voice)
    target = "skipped" if skip else "marked"
    if result.get(target) is not None:
        result[target] = scoped_key(profile, result[target])
        for dose_id in result["doseIds"]:
            record = profile["store"]["doses"][dose_id]
            if record["state"] == "TAKEN":
                record.setdefault("recordedBy", "account_owner")
        result["doseIds"] = [scoped_key(profile, value) for value in result["doseIds"]]
        if result.get("say"):
            result["say"] = f"[{profile['alias']}] " + result["say"]
    return _result(profile, result)


def due(family, at, voice, reference=None):
    at = at.astimezone(SEOUL)
    profiles = [resolve_profile(family, reference)] if reference is not None else family["profiles"].values()
    send = []
    for profile in profiles:
        result = meds.store_due(profile["store"], at, voice)
        for message in result["send"]:
            send.append({**message, "key": scoped_key(profile, message["key"]), "profileId": profile["id"],
                         "profileAlias": profile["alias"], "say": f"[{profile['alias']}] " + message["say"]})
        for appointment in profile["appointments"].values():
            if appointment["cancelledAt"] is not None:
                continue
            all_day = appointment.get("allDayDate") is not None
            start = datetime.combine(parse_date(appointment["allDayDate"]), time(), SEOUL) if all_day else parse_instant(appointment["start"])
            expires = start + timedelta(days=1) if all_day else start
            if at >= expires or is_quiet(at):
                continue
            for days in appointment.get("reminderDays", [7, 1]):
                try:
                    day = start.date() - timedelta(days=days)
                except OverflowError:
                    continue
                reminder_time = appointment.get("reminderTime", "09:00")
                target = datetime.combine(day, time.fromisoformat(reminder_time), SEOUL)
                reminder_key = f"{day.isoformat()}|{days}|{reminder_time}"
                sent = appointment.setdefault("sentReminders", {})
                if (at.date() != day or at < target or target < parse_instant(appointment["confirmedAt"])
                        or reminder_key in sent):
                    continue
                sent[reminder_key] = iso(at)
                companion = f" · 동행: {appointment['caregiver']}" if appointment.get("caregiver") else ""
                when = start.date().isoformat() + (" 종일" if all_day else f" {start:%H:%M}")
                send.append({"key": scoped_key(profile, "appointment|" + appointment["id"] + "|reminder|" + reminder_key),
                             "profileId": profile["id"], "profileAlias": profile["alias"], "kind": "appointment", "step": 1,
                             "say": f"[{profile['alias']}] {appointment['title']} 일정이 {days}일 뒤예요 🥹 · {when}{companion}"})
    return {"now": iso(at), "send": send}


def _appointment_fields(value):
    _name(value.get("title"), "title", 100)
    caregiver = value.get("caregiver")
    if caregiver is not None:
        caregiver = _name(caregiver, "caregiver", 30)
    days = value.get("reminderDays", [7, 1])
    if (not isinstance(days, list) or len(days) > 7 or any(type(day) is not int or not 0 <= day <= 365 for day in days)
            or len(set(days)) != len(days)):
        raise ToolError("INVALID_INPUT", "알림 일수는 중복 없는 0~365 정수 최대7개입니다.")
    common = {"title": value["title"].strip(), "caregiver": caregiver, "reminderDays": sorted(days, reverse=True),
              "reminderTime": parse_hhmm(value.get("reminderTime", "09:00"), "reminderTime")}
    if value.get("allDayDate") is not None:
        if value.get("start") is not None or value.get("end") is not None:
            raise ToolError("INVALID_INPUT", "종일 일정과 시간제 일정을 섞을 수 없습니다.")
        return {**common, "allDayDate": parse_date(value["allDayDate"]).isoformat()}
    start, end = parse_instant(value.get("start"), "start"), parse_instant(value.get("end"), "end")
    if end <= start:
        raise ToolError("INVALID_INPUT", "일정 종료는 시작 뒤여야 합니다.")
    return {**common, "start": iso(start), "end": iso(end)}


def appointment_action(family, reference, operation, at, payload=None, appointment_id=None):
    profile = resolve_profile(family, reference)
    appointments = profile["appointments"]
    if operation == "list":
        return _result(profile, {"appointments": [deepcopy(a) for a in appointments.values() if a["cancelledAt"] is None]})
    if operation == "add":
        if not isinstance(payload, dict) or set(payload) - {"title", "start", "end", "allDayDate", "caregiver", "reminderDays", "reminderTime"}:
            raise ToolError("INVALID_INPUT", "캘린더 일정 입력 필드를 확인해 주세요.")
        fields = _appointment_fields(payload)
        appointment_id = "appt_" + uuid4().hex
        appointments[appointment_id] = {**fields, "id": appointment_id, "confirmedAt": iso(at), "cancelledAt": None, "history": [], "sentReminders": {}}
    else:
        if not isinstance(appointment_id, str) or not appointment_id:
            raise ToolError("INVALID_INPUT", "대상 일정 ID를 확인해 주세요.")
        appointment = appointments.get(appointment_id)
        if appointment is None:
            raise ToolError("APPOINTMENT_NOT_FOUND", "해당 프로필의 캘린더 일정이 아닙니다.")
        if operation == "cancel":
            if appointment["cancelledAt"] is None:
                appointment["cancelledAt"] = iso(at)
                key = "appointment|" + appointment_id
                applied = profile["store"]["calendar"]["applied"].get(key)
                if applied:
                    profile["store"]["calendar"]["pending"][key] = {"operation": "DELETE"}
        elif operation == "update":
            if appointment["cancelledAt"] is not None:
                raise ToolError("APPOINTMENT_CANCELLED", "이미 취소한 캘린더 일정입니다.")
            if not isinstance(payload, dict) or not payload or set(payload) - {"title", "start", "end", "allDayDate", "caregiver", "reminderDays", "reminderTime"}:
                raise ToolError("INVALID_INPUT", "캘린더 일정 변경 필드를 확인해 주세요.")
            candidate = {k: appointment[k] for k in ("title", "start", "end", "allDayDate", "caregiver", "reminderDays", "reminderTime") if k in appointment}
            if payload.get("allDayDate") is not None:
                candidate.pop("start", None)
                candidate.pop("end", None)
            elif payload.get("start") is not None or payload.get("end") is not None:
                candidate.pop("allDayDate", None)
            candidate.update(payload)
            fields = _appointment_fields(candidate)
            appointment["history"].append({"at": iso(at), "previous": {k: deepcopy(appointment.get(k)) for k in ("title", "start", "end", "allDayDate", "caregiver", "reminderDays", "reminderTime")}})
            for key in ("start", "end", "allDayDate"):
                appointment.pop(key, None)
            appointment.update(fields, confirmedAt=iso(at))
        else:
            raise ToolError("INVALID_INPUT", "지원하지 않는 캘린더 일정 명령입니다.")
    return _result(profile, {"appointmentId": appointment_id, "appointment": deepcopy(appointments[appointment_id])})


def _appointment_events(profile):
    result = []
    for appointment in profile["appointments"].values():
        if appointment["cancelledAt"] is not None:
            continue
        key = "appointment|" + appointment["id"]
        companion = f"\n동행: {appointment['caregiver']}" if appointment.get("caregiver") else ""
        reminders = "·".join(str(day) for day in appointment.get("reminderDays", [7, 1]))
        event = {"key": key, "summary": appointment["title"], "description": f"사용자가 확인한 캘린더 일정{companion}\n그록 알림: {reminders}일 전 {appointment.get('reminderTime', '09:00')}\nkey: {key}"}
        event.update({k: appointment[k] for k in ("start", "end", "allDayDate") if k in appointment})
        result.append(event)
    return result


def _scope_event(profile, event):
    result = deepcopy(event)
    result["key"] = scoped_key(profile, event["key"])
    result["summary"] = f"[{profile['alias']}] " + event["summary"]
    lines = [line for line in event["description"].splitlines() if not line.startswith("key: ")]
    result["description"] = "\n".join([f"대상 프로필: {profile['alias']}", *lines, "key: " + result["key"]])
    return result


def schedule(family, reference=None):
    profiles = [resolve_profile(family, reference)] if reference is not None else family["profiles"].values()
    occurrences, events, revisits = [], [], []
    for profile in profiles:
        view = meds.build_store_schedule(profile["store"])
        for occurrence in view["occurrences"]:
            occurrences.append({**occurrence, "key": scoped_key(profile, occurrence["key"]),
                                "profileId": profile["id"], "profileAlias": profile["alias"]})
        events.extend(_scope_event(profile, event) for event in [*view["calendarEvents"], *_appointment_events(profile)])
        revisits.append({"profileId": profile["id"], "dates": view["revisitDates"]})
    return {"schemaVersion": 3, "revision": family["revision"], "occurrences": sorted(occurrences, key=lambda o: (o["at"], o["profileId"])),
            "calendarEvents": events, "revisits": revisits}


def calendar_plan(family, reference=None):
    profiles = [resolve_profile(family, reference)] if reference is not None else family["profiles"].values()
    changes = []
    for profile in profiles:
        for raw in meds.calendar_plan(profile["store"])["changes"]:
            job = {**deepcopy(raw), "key": scoped_key(profile, raw["key"]), "profileId": profile["id"],
                   "profileAlias": profile["alias"], "event": _scope_event(profile, raw["event"]) if raw["event"] else None}
            job["fingerprint"] = meds._hash({"profileId": profile["id"], "alias": profile["alias"], "raw": raw, "event": job["event"]})
            if raw["key"] in profile["legacyKeys"]:
                job["legacyKey"] = raw["key"]
            changes.append(job)
    return {"changes": sorted(changes, key=lambda job: job["key"])}


def acknowledge(family, key, fingerprint, event_id=None, reference=None):
    prefix = key.partition("|")[0]
    profile = resolve_profile(family, reference if reference is not None else prefix if prefix in family["profiles"] else None)
    inner = _inner_key(profile, key)
    external = scoped_key(profile, inner)
    jobs = {job["key"]: job for job in calendar_plan(family, profile["id"])["changes"]}
    job = jobs.get(external)
    if job is None and profile["calendarAcks"].get(external) == fingerprint:
        return _result(profile, {"acknowledged": external, "replayed": True})
    if job is None or job["fingerprint"] != fingerprint:
        raise ToolError("STALE_CALENDAR_CHANGE", "현재 프로필의 캘린더 작업을 다시 확인해 주세요.")
    raw = profile["store"]["calendar"]["pending"][inner]
    result = meds.acknowledge_calendar(profile["store"], inner, raw["fingerprint"], event_id)
    profile["calendarAcks"][external] = fingerprint
    if inner in profile["legacyKeys"]:
        profile["legacyKeys"].remove(inner)
    return _result(profile, {**result, "acknowledged": external})


def _refresh(family, at):
    for profile in family["profiles"].values():
        meds.refresh_calendar(profile["store"], at, _appointment_events(profile))


def _load(path, at):
    raw = read_json(path) if path.exists() else None
    if isinstance(raw, dict) and raw.get("schemaVersion") == 3:
        return validate_family(raw), None, False
    substore = meds.validate_store(deepcopy(raw)) if raw is not None else meds.empty_store()
    if raw is None:
        meds._migrate_legacy(substore, path.parent, at)
    has_records = bool(substore["medicines"] or substore["doses"] or substore["calendar"]["applied"])
    profile = _profile("profile_legacy" if has_records else "profile_self", "기존 약" if has_records else "나", at, substore)
    if has_records:
        profile["legacyKeys"] = list(dict.fromkeys([*(e["key"] for e in meds.build_store_schedule(substore)["calendarEvents"]),
                                                    *substore["calendar"]["applied"]]))
        for applied in substore["calendar"]["applied"].values():
            applied["contentHash"] = "profile-scope-migration"
    family = {"schemaVersion": 3, "revision": substore["revision"], "profiles": {profile["id"]: profile},
              "drafts": {}, "intakeRequests": {}, "migration": {"from": 2, "profileId": profile["id"], "at": iso(at)} if raw is not None or has_records else None}
    return family, raw, True


def transact(path, action, at=None, expected_revision=None, include_changes=True):
    at = (at or datetime.now(SEOUL)).astimezone(SEOUL)
    path = Path(path).resolve()
    with state_lock(path):
        family, backup, converted = _load(path, at)
        before = deepcopy(family)
        if expected_revision is not None and expected_revision != family["revision"]:
            raise ToolError("REVISION_CONFLICT", "가족 목록이 바뀌었습니다. 최신 초안을 확인해 주세요.")
        _refresh(family, at)
        result = action(family)
        validate_family(family)
        _refresh(family, at)
        validate_family(family)
        if converted or family != before:
            family["revision"] = before["revision"] + 1
            if backup is not None:
                backup_path = path.parent / "backups" / (path.stem + "-v2-" + meds._hash(backup)[:16] + ".json")
                if not backup_path.exists():
                    atomic_write_json(backup_path, backup)
                elif meds._hash(read_json(backup_path)) != meds._hash(backup):
                    raise ToolError("BACKUP_CONFLICT", "이관 원본 백업을 확인해 주세요.")
            atomic_write_json(path, family)
        result["revision"] = family["revision"]
        if include_changes:
            result["calendarChanges"] = calendar_plan(family, result.get("profileId"))["changes"]
        return result
