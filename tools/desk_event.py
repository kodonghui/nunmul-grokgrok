"""Validate one authenticated Grok desk event and atomically record its exact doses."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

if __package__:
    from .common import JsonArgumentParser, SEOUL, ToolError, iso, parse_instant, read_json, run_cli
    from .dose_rules import VOICE_PATH
    from . import family_store as family
    from . import med_store as meds
else:
    from common import JsonArgumentParser, SEOUL, ToolError, iso, parse_instant, read_json, run_cli
    from dose_rules import VOICE_PATH
    import family_store as family
    import med_store as meds


FIELDS = {"event", "source", "pressedAt", "profileId", "key", "doseIds", "medicineIds", "scheduleRevision", "requestId"}
PROFILE = re.compile(r"profile_[a-z0-9_]{1,72}\Z")
MEDICINE = re.compile(r"med_[A-Za-z0-9_]{1,72}\Z")
DOSE = re.compile(r"med_[A-Za-z0-9_]{1,72}\|v[1-9][0-9]*\|[0-9]{4}-[0-9]{2}-[0-9]{2}\|(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


def _invalid():
    return ToolError("INVALID_DESK_EVENT", "책상 이벤트의 허용 필드·식별자·형식을 확인해 주세요.")


def request_id(profile_id, key, dose_ids):
    identity = {"profileId": profile_id, "key": key, "doseIds": dose_ids}
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "desk_" + hashlib.sha256(raw).hexdigest()


def validate_event(payload, bound_profile):
    if (not isinstance(bound_profile, str) or not PROFILE.fullmatch(bound_profile)
            or not isinstance(payload, dict) or set(payload) != FIELDS
            or payload["event"] != "dose_taken" or payload["source"] != "desk"):
        raise _invalid()
    if payload["profileId"] != bound_profile:
        raise ToolError("PROFILE_MISMATCH", "접수 경로에 연결한 프로필과 버튼 대상이 다릅니다.")
    key = payload["key"]
    if not isinstance(key, str) or not key.startswith(bound_profile + "|"):
        raise ToolError("PROFILE_MISMATCH", "버튼 회차와 연결한 프로필이 다릅니다.")
    inner = key[len(bound_profile) + 1:]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\|(?:[01]\d|2[0-3]):[0-5]\d", inner):
        raise _invalid()
    scheduled = parse_instant(inner.replace("|", "T") + ":00+09:00", "key")
    pressed = parse_instant(payload["pressedAt"], "pressedAt")
    if type(payload["scheduleRevision"]) is not int or payload["scheduleRevision"] < 0:
        raise _invalid()
    for field in ("doseIds", "medicineIds"):
        values = payload[field]
        if (not isinstance(values, list) or not 1 <= len(values) <= 100
                or any(not isinstance(value, str) or not value or len(value) > 200 for value in values)
                or len(set(values)) != len(values)):
            raise _invalid()
    if len(payload["medicineIds"]) != len(payload["doseIds"]) or any(not MEDICINE.fullmatch(m) for m in payload["medicineIds"]):
        raise _invalid()
    if any(not DOSE.fullmatch(value) for value in payload["doseIds"]):
        raise _invalid()
    supplied_id = payload["requestId"]
    if not isinstance(supplied_id, str) or supplied_id != request_id(bound_profile, key, payload["doseIds"]):
        raise _invalid()
    value = deepcopy(payload)
    value["pressedAt"] = iso(pressed)
    return value, scheduled, pressed


def accept(store, payload, bound_profile, now):
    event, scheduled, pressed = validate_event(payload, bound_profile)
    profile = family.resolve_profile(store, bound_profile)
    digest = meds._hash(event)
    ledger = profile.get("deskRequests", {})
    previous = ledger.get(event["requestId"])
    if previous is not None:
        if previous["digest"] != digest:
            raise ToolError("REQUEST_CONFLICT", "같은 책상 요청 ID의 내용이 다릅니다.")
        return {**deepcopy(previous["result"]), "replayed": True, "notify": False, "say": None}
    if not -120 <= (now - pressed).total_seconds() <= 600 or not -3600 <= (pressed - scheduled).total_seconds() <= 5400:
        raise ToolError("DESK_TIME_WINDOW", "버튼 시각과 회차·수신 시각의 허용 범위를 확인해 주세요.")
    if event["scheduleRevision"] > store["revision"]:
        raise ToolError("DESK_STALE_SCHEDULE", "서버와 책상 일정의 버전을 다시 확인해 주세요.")
    substore = profile["store"]
    selected = []
    for dose_id in event["doseIds"]:
        record = substore["doses"].get(dose_id)
        if record is None or record["medicineId"] not in event["medicineIds"] or parse_instant(record["at"]) != scheduled:
            raise ToolError("DESK_TARGET_MISMATCH", "해당 프로필·회차의 약별 기록과 요청이 다릅니다.")
        if pressed < parse_instant(record["confirmedAt"]):
            raise ToolError("DESK_TIME_WINDOW", "일정 확인 전에 누른 버튼 요청은 기록하지 않습니다.")
        med = substore["medicines"][record["medicineId"]]
        regimen = med["schedule"]
        if (med["deletedAt"] is not None or regimen is None or regimen["stoppedAt"] is not None
                or not record["active"] or record["state"] not in ("PENDING", "TAKEN")
                or record["scheduleVersion"] != regimen["version"]
                or not regimen["startDate"] <= scheduled.date().isoformat() <= regimen["endDate"]
                or scheduled.strftime("%H:%M") not in regimen["doseTimes"]):
            raise ToolError("DESK_STALE_SCHEDULE", "중단·삭제·변경된 약의 버튼 요청은 기록하지 않습니다.")
        selected.append((dose_id, record))
    if {r["medicineId"] for _, r in selected} != set(event["medicineIds"]):
        raise ToolError("DESK_TARGET_MISMATCH", "요청의 약 ID와 약별 회차 ID가 일치하지 않습니다.")
    current = {dose_id for dose_id, r in substore["doses"].items()
               if r["active"] and r["medicineId"] in event["medicineIds"] and parse_instant(r["at"]) == scheduled}
    if current != set(event["doseIds"]):
        raise ToolError("DESK_STALE_SCHEDULE", "현재 약별 회차와 책상 일정이 다릅니다.")
    changed = [family.scoped_key(profile, dose_id) for dose_id, record in selected if record["state"] == "PENDING"]
    result = family.mark(store, bound_profile, pressed, "desk", event["key"], event["medicineIds"], voice=read_json(VOICE_PATH))
    result.update(requestId=event["requestId"], recorded=True, replayed=False, notify=bool(changed), changedDoseIds=changed)
    if not changed:
        result["say"] = None
    profile.setdefault("deskRequests", {})[event["requestId"]] = {"digest": digest, "receivedAt": iso(now), "result": deepcopy(result)}
    return result


def read_event(path):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def reject(_):
        raise ValueError
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=reject)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    except OSError:
        raise ToolError("READ_FAILED", "책상 이벤트 파일을 읽을 수 없습니다.") from None


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--store", type=Path, default=family.DEFAULT_STORE)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    if args.input.resolve() == args.store.resolve():
        raise ToolError("INVALID_ARGUMENT", "이벤트 파일과 원장은 서로 달라야 합니다.")
    payload = read_event(args.input)
    validate_event(payload, args.profile)
    now = parse_instant(args.now) if args.now else datetime.now(SEOUL)
    return family.transact(args.store, lambda store: accept(store, payload, args.profile, now), now, require_existing=True)


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
