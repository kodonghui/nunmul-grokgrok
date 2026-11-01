"""Atomic, cumulative medicine records and calendar reconciliation data."""

from copy import deepcopy
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

if __package__:
    from .common import (SEOUL, ToolError, atomic_write_json, iso, parse_date,
                         parse_hhmm, parse_instant, read_json, state_lock)
    from .dose_rules import is_quiet, phase_at, validate_voice
    from .make_schedule import NOTICE, _slots, _times, build_schedule, time_label
else:
    from common import (SEOUL, ToolError, atomic_write_json, iso, parse_date,
                        parse_hhmm, parse_instant, read_json, state_lock)
    from dose_rules import is_quiet, phase_at, validate_voice
    from make_schedule import NOTICE, _slots, _times, build_schedule, time_label


DEFAULT_STORE = Path("state/medication_store.json")
STATES = {"PENDING", "TAKEN", "MISSED", "SKIPPED"}
SCHEDULE_FIELDS = {"startDate", "endDate", "durationDays", "dailyCount", "dosePeriods", "doseTimes"}
PATCH_FIELDS = SCHEDULE_FIELDS | {"dosePerTime", "timing"}


def empty_store():
    return {"schemaVersion": 2, "revision": 0, "medicines": {}, "doses": {},
            "reminders": {}, "requests": {},
            "calendar": {"applied": {}, "pending": {}, "acks": {}}, "migration": None}


def _hash(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        raise ToolError("INVALID_INPUT", "JSON 입력 값의 형식을 확인해 주세요.") from None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_store(store):
    if (not isinstance(store, dict) or store.get("schemaVersion") != 2
            or type(store.get("revision")) is not int or store["revision"] < 0
            or any(not isinstance(store.get(k), dict) for k in ("medicines", "doses", "reminders", "requests", "calendar"))
            or any(not isinstance(store["calendar"].get(k), dict) for k in ("applied", "pending", "acks"))):
        raise ToolError("INVALID_STORE", "내 약 원장의 형식과 버전을 확인해 주세요.")
    for medicine_id, med in store["medicines"].items():
        required = {"id", "itemSeq", "name", "dosePerTime", "timing", "registeredAt", "deletedAt", "scheduleVersion", "schedule", "history"}
        if (not isinstance(med, dict) or not required.issubset(med) or med.get("id") != medicine_id
                or not re.fullmatch(r"med_[0-9a-f]{32}", medicine_id)
                or not isinstance(med.get("name"), str) or not med["name"].strip()
                or type(med.get("scheduleVersion")) is not int or med["scheduleVersion"] < 0
                or not isinstance(med.get("history"), list)):
            raise ToolError("INVALID_STORE", "저장된 약 항목을 확인해 주세요.")
        parse_instant(med["registeredAt"], "registeredAt")
        if med["deletedAt"] is not None:
            parse_instant(med["deletedAt"], "deletedAt")
        if med["itemSeq"] is not None and (not isinstance(med["itemSeq"], str) or not re.fullmatch(r"[0-9]+", med["itemSeq"])):
            raise ToolError("INVALID_STORE", "저장된 품목코드를 확인해 주세요.")
        _amount(med["dosePerTime"], optional=True)
        _timing(med["timing"])
        if med.get("schedule") is not None and not isinstance(med["schedule"], dict):
            raise ToolError("INVALID_STORE", "저장된 약별 일정을 확인해 주세요.")
        regimen = med["schedule"]
        if regimen is not None:
            _amount(med["dosePerTime"])
            required_schedule = {"version", "startDate", "endDate", "doseTimes", "confirmedAt", "stoppedAt", "revisitDate"}
            if (not required_schedule.issubset(regimen) or type(regimen["version"]) is not int
                    or regimen["version"] != med["scheduleVersion"] or regimen["version"] < 1
                    or not isinstance(regimen["doseTimes"], list) or not 1 <= len(regimen["doseTimes"]) <= 3):
                raise ToolError("INVALID_STORE", "저장된 약별 일정 필드를 확인해 주세요.")
            if parse_date(regimen["endDate"]) < parse_date(regimen["startDate"]):
                raise ToolError("INVALID_STORE", "저장된 일정 기간을 확인해 주세요.")
            times = [parse_hhmm(value) for value in regimen["doseTimes"]]
            if len(set(times)) != len(times) or sorted(times) != times:
                raise ToolError("INVALID_STORE", "저장된 일정 시각의 중복/순서를 확인해 주세요.")
            parse_instant(regimen["confirmedAt"], "confirmedAt")
            parse_date(regimen["revisitDate"], "revisitDate")
            if regimen["stoppedAt"] is not None:
                parse_instant(regimen["stoppedAt"], "stoppedAt")
    for dose_id, record in store["doses"].items():
        required_dose = {"medicineId", "scheduleVersion", "at", "name", "line", "confirmedAt",
                         "state", "sentStep", "takenAt", "source", "active", "skipReason"}
        if (not isinstance(record, dict) or not required_dose.issubset(record)
                or not isinstance(record.get("medicineId"), str)
                or record["medicineId"] not in store["medicines"]
                or not isinstance(record.get("name"), str) or not record["name"].strip()
                or not isinstance(record.get("state"), str) or record["state"] not in STATES
                or type(record.get("sentStep")) is not int or not 0 <= record["sentStep"] <= 4
                or type(record.get("active")) is not bool or not isinstance(record.get("line"), str)
                or type(record.get("scheduleVersion")) is not int
                or not 1 <= record["scheduleVersion"] <= store["medicines"][record["medicineId"]]["scheduleVersion"]):
            raise ToolError("INVALID_STORE", "저장된 약별 복용 기록을 확인해 주세요.")
        at = parse_instant(record.get("at"), "dose.at")
        parse_instant(record["confirmedAt"], "dose.confirmedAt")
        expected = f"{record['medicineId']}|v{record['scheduleVersion']}|{at.date().isoformat()}|{at:%H:%M}"
        if dose_id != expected or at.second or at.microsecond:
            raise ToolError("INVALID_STORE", "복용 기록 ID와 시각이 다릅니다.")
        if record["state"] == "TAKEN":
            parse_instant(record.get("takenAt"), "takenAt")
            if record.get("source") not in ("chat", "desk"):
                raise ToolError("INVALID_STORE", "복용 확인 출처를 확인해 주세요.")
    for entry in store["reminders"].values():
        if not isinstance(entry, dict) or type(entry.get("sentStep")) is not int or not 0 <= entry["sentStep"] <= 4:
            raise ToolError("INVALID_STORE", "재촉 단계 기록을 확인해 주세요.")
    for entry in store["requests"].values():
        if (not isinstance(entry, dict) or not isinstance(entry.get("digest"), str)
                or not isinstance(entry.get("medicineIds"), list)
                or any(not isinstance(m, str) or m not in store["medicines"] for m in entry["medicineIds"])):
            raise ToolError("INVALID_STORE", "등록 요청 기록을 확인해 주세요.")
    for key, entry in store["calendar"]["applied"].items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("eventId"), str)
                or not isinstance(entry.get("contentHash"), str) or not isinstance(entry.get("event"), dict)
                or entry["event"].get("key") != key):
            raise ToolError("INVALID_STORE", "캘린더 반영 기록을 확인해 주세요.")
        _validate_event(entry["event"])
    for entry in store["calendar"]["pending"].values():
        if not isinstance(entry, dict) or entry.get("operation") not in ("UPSERT", "DELETE"):
            raise ToolError("INVALID_STORE", "캘린더 대기 작업을 확인해 주세요.")
    return store


def _validate_event(event):
    if "allDayDate" in event:
        parse_date(event["allDayDate"])
    else:
        parse_instant(event.get("start"))
        parse_instant(event.get("end"))


def _amount(value, optional=False):
    if optional and value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 < value <= 10 or value * 2 % 1 != 0):
        raise ToolError("INVALID_INPUT", "dosePerTime: 0보다 크고 10 이하인 0.5 단위 숫자가 필요합니다.")
    return value


def _timing(value):
    if value is not None and not isinstance(value, str):
        raise ToolError("INVALID_INPUT", "timing: 문구 또는 null이 필요합니다.")
    return value.strip() or None if isinstance(value, str) else None


def _identity(item):
    if item.get("itemSeq") is not None:
        return ("official", item["itemSeq"])
    return ("manual", re.sub(r"\s+", "", unicodedata.normalize("NFKC", item["name"])).casefold())


def _medicine(store, medicine_id, include_deleted=False):
    med = store["medicines"].get(medicine_id)
    if med is None:
        raise ToolError("MEDICINE_NOT_FOUND", "등록된 약을 찾을 수 없습니다.")
    if med["deletedAt"] is not None and not include_deleted:
        raise ToolError("MEDICINE_DELETED", "현재 목록에서 삭제된 약입니다.")
    return med


def make_regimen(med, patch, at, slots=None):
    old = med.get("schedule") or {}
    start = parse_date(patch.get("startDate", old.get("startDate")), "startDate")
    if patch.get("durationDays") is not None:
        days = patch["durationDays"]
        if type(days) is not int or days < 1:
            raise ToolError("INVALID_INPUT", "durationDays: 1 이상의 일수가 필요합니다.")
        try:
            end = start + timedelta(days=days - 1)
        except OverflowError:
            raise ToolError("INVALID_INPUT", "계산할 수 있는 날짜 범위를 넘었습니다.") from None
        if patch.get("endDate") is not None and parse_date(patch["endDate"], "endDate") != end:
            raise ToolError("INVALID_INPUT", "일수와 종료일이 다릅니다.")
    else:
        end = parse_date(patch.get("endDate", old.get("endDate")), "endDate")
    if end < start:
        raise ToolError("INVALID_INPUT", "종료일은 시작일보다 빠를 수 없습니다.")
    if patch.get("doseTimes") is not None:
        values = patch["doseTimes"]
        if not isinstance(values, list) or not 1 <= len(values) <= 3:
            raise ToolError("INVALID_INPUT", "doseTimes: 복용 시각 1~3개가 필요합니다.")
        times = sorted(parse_hhmm(t, "doseTimes") for t in values)
        if len(set(times)) != len(times):
            raise ToolError("INVALID_INPUT", "복용 시각은 서로 달라야 합니다.")
        if patch.get("dailyCount") is not None and (type(patch["dailyCount"]) is not int or patch["dailyCount"] != len(times)):
            raise ToolError("INVALID_INPUT", "횟수와 시각 개수가 다릅니다.")
        if patch.get("dosePeriods") is not None:
            expected = _times({"dosePeriods": patch["dosePeriods"], "dailyCount": len(times)}, _slots(slots))
            if times != expected:
                raise ToolError("INVALID_INPUT", "복용 때와 직접 지정한 시각이 다릅니다.")
    elif patch.get("dosePeriods") is not None or patch.get("dailyCount") is not None:
        times = _times(patch, _slots(slots))
    elif old.get("doseTimes"):
        times = list(old["doseTimes"])
    else:
        raise ToolError("INVALID_INPUT", "복용 횟수나 시각을 확인해 주세요.")
    amount = _amount(patch.get("dosePerTime", med.get("dosePerTime")))
    timing = _timing(patch.get("timing", med.get("timing")))
    revisit = max(date.fromordinal(max(1, end.toordinal() - 3)), at.date())
    return ({"version": med["scheduleVersion"] + 1, "startDate": start.isoformat(),
             "endDate": end.isoformat(), "doseTimes": times, "confirmedAt": iso(at),
             "stoppedAt": None, "revisitDate": revisit.isoformat()}, amount, timing)


def _materialize(store, med, changed_at=None):
    regimen = med["schedule"]
    start, end = parse_date(regimen["startDate"]), parse_date(regimen["endDate"])
    confirmed = parse_instant(regimen["confirmedAt"])
    line = f"{med['name']} 1회 {med['dosePerTime']:g}개"
    if med["timing"]:
        line += f" · {med['timing']}"
    taken = {d["at"]: d for d in store["doses"].values()
             if d["medicineId"] == med["id"] and d["state"] == "TAKEN"}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        for hhmm in regimen["doseTimes"]:
            at = datetime.combine(day, time.fromisoformat(hhmm), SEOUL)
            if changed_at is not None and at < changed_at:
                continue
            if iso(at) in taken:
                taken[iso(at)]["active"] = True
                continue
            key = f"{med['id']}|v{regimen['version']}|{day.isoformat()}|{hhmm}"
            store["doses"][key] = {
                "medicineId": med["id"], "scheduleVersion": regimen["version"],
                "at": iso(at), "name": med["name"], "line": line, "confirmedAt": iso(confirmed),
                "state": "SKIPPED" if at < confirmed else "PENDING", "sentStep": 0,
                "takenAt": None, "source": None, "active": True,
                "skipReason": "BEFORE_CONFIRMATION" if at < confirmed else None,
            }


def add_medicines(store, payload, at, request_id=None, slots=None):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or not payload["items"]:
        raise ToolError("INVALID_INPUT", "확인된 items 목록이 필요합니다.")
    fields = PATCH_FIELDS | {"name", "itemSeq", "confirmedAt"}
    effective = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise ToolError("INVALID_INPUT", "약 항목 객체가 필요합니다.")
        effective.append({k: item.get(k, payload.get(k)) for k in fields})
    digest = _hash(effective)
    request_id = request_id or payload.get("requestId") or "auto:" + digest
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise ToolError("INVALID_INPUT", "requestId를 확인해 주세요.")
    previous = store["requests"].get(request_id)
    if previous is not None:
        if previous["digest"] != digest:
            raise ToolError("REQUEST_CONFLICT", "같은 등록 요청 ID의 내용이 다릅니다.")
        return {"added": previous["medicineIds"], "replayed": True}
    existing = {_identity(m) for m in store["medicines"].values() if m["deletedAt"] is None}
    prepared = []
    for item in effective:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolError("INVALID_INPUT", "확인된 약 이름이 필요합니다.")
        code = item.get("itemSeq")
        if code is not None:
            if isinstance(code, bool) or not re.fullmatch(r"[0-9]+", str(code)):
                raise ToolError("INVALID_INPUT", "itemSeq: 숫자 품목코드 또는 null이 필요합니다.")
            code = str(code)
        med = {"id": "med_" + uuid4().hex, "itemSeq": code, "name": name.strip(),
               "dosePerTime": _amount(item.get("dosePerTime"), optional=True),
               "timing": _timing(item.get("timing")), "registeredAt": iso(at),
               "deletedAt": None, "scheduleVersion": 0, "schedule": None, "history": []}
        identity = _identity(med)
        if identity in existing:
            raise ToolError("DUPLICATE_MEDICINE", "이미 등록한 약입니다. 해당 약의 일정 변경을 사용해 주세요.")
        existing.add(identity)
        if any(item.get(k) is not None for k in SCHEDULE_FIELDS):
            confirmed = parse_instant(item["confirmedAt"], "confirmedAt") if item["confirmedAt"] is not None else at
            regimen, amount, timing = make_regimen(med, item, confirmed, slots)
            med.update(schedule=regimen, scheduleVersion=regimen["version"], dosePerTime=amount, timing=timing)
        prepared.append(med)
    for med in prepared:
        store["medicines"][med["id"]] = med
        if med["schedule"] is not None:
            _materialize(store, med)
    ids = [m["id"] for m in prepared]
    store["requests"][request_id] = {"digest": digest, "medicineIds": ids}
    return {"added": ids, "replayed": False}


def _retire(store, med, at, reason, all_pending=False):
    for record in store["doses"].values():
        if record["medicineId"] != med["id"] or not record["active"]:
            continue
        future = parse_instant(record["at"]) >= at
        if record["state"] == "PENDING" and (future or all_pending):
            record.update(state="SKIPPED", skipReason=reason, active=False)
        elif future and (all_pending or record["state"] != "TAKEN"):
            record["active"] = False


def set_schedule(store, medicine_id, patch, at, slots=None):
    med = _medicine(store, medicine_id)
    if not isinstance(patch, dict) or not patch or set(patch) - PATCH_FIELDS:
        raise ToolError("INVALID_INPUT", "일정 변경 필드를 확인해 주세요.")
    regimen, amount, timing = make_regimen(med, patch, at, slots)
    old = med["schedule"]
    if (old is not None and old["stoppedAt"] is None and amount == med["dosePerTime"] and timing == med["timing"]
            and all(regimen[k] == old[k] for k in ("startDate", "endDate", "doseTimes"))):
        return {"updated": medicine_id, "unchanged": True}
    if old is not None:
        med["history"].append({"action": "CHANGE", "at": iso(at), "schedule": deepcopy(old)})
    _retire(store, med, at, "SCHEDULE_CHANGED")
    med.update(schedule=regimen, scheduleVersion=regimen["version"], dosePerTime=amount, timing=timing)
    _materialize(store, med, at if old is not None or med["scheduleVersion"] > 1 else None)
    return {"updated": medicine_id, "unchanged": False}


def stop_medicine(store, medicine_id, at):
    med = _medicine(store, medicine_id)
    regimen = med["schedule"]
    if regimen is None:
        raise ToolError("NO_SCHEDULE", "등록된 복용 일정이 없습니다.")
    if regimen["stoppedAt"] is not None:
        return {"stopped": medicine_id}
    if not parse_date(regimen["startDate"]) <= at.date() <= parse_date(regimen["endDate"]):
        raise ToolError("SCHEDULE_CONFLICT", "현재 진행 중인 일정이 아닙니다. 일정 삭제를 사용할 수 있습니다.")
    med["history"].append({"action": "STOP", "at": iso(at), "schedule": deepcopy(regimen)})
    regimen.update(endDate=at.date().isoformat(), stoppedAt=iso(at))
    _retire(store, med, at, "STOPPED", all_pending=True)
    return {"stopped": medicine_id}


def clear_schedule(store, medicine_id, at):
    med = _medicine(store, medicine_id)
    if med["schedule"] is not None:
        med["history"].append({"action": "CLEAR", "at": iso(at), "schedule": deepcopy(med["schedule"])})
        _retire(store, med, at, "SCHEDULE_CLEARED", all_pending=True)
        med["schedule"] = None
    return {"cleared": medicine_id}


def delete_medicine(store, medicine_id, at):
    med = _medicine(store, medicine_id, include_deleted=True)
    if med["deletedAt"] is None:
        med["deletedAt"] = iso(at)
        _retire(store, med, at, "DELETED", all_pending=True)
    return {"deleted": medicine_id}


def list_medicines(store, at, include_deleted=False):
    result = []
    for med in sorted(store["medicines"].values(), key=lambda m: (m["registeredAt"], m["id"])):
        if med["deletedAt"] is not None and not include_deleted:
            continue
        schedule = med["schedule"]
        status = "DELETED" if med["deletedAt"] else "UNSCHEDULED" if schedule is None else (
            "STOPPED" if schedule["stoppedAt"] else "FINISHED" if parse_date(schedule["endDate"]) < at.date()
            else "SCHEDULED" if parse_date(schedule["startDate"]) > at.date() else "ACTIVE")
        result.append({k: deepcopy(med[k]) for k in ("id", "itemSeq", "name", "dosePerTime", "timing", "registeredAt", "deletedAt", "schedule")})
        result[-1]["status"] = status
    return {"medicines": result}


def _groups(store):
    result = {}
    for dose_id, dose in store["doses"].items():
        if dose["active"]:
            at = parse_instant(dose["at"])
            key = f"{at.date().isoformat()}|{at:%H:%M}"
            result.setdefault(key, []).append((dose_id, dose))
    return {key: sorted(rows, key=lambda row: row[0]) for key, rows in sorted(result.items())}


def _revisits(store):
    result = {}
    for med in store["medicines"].values():
        regimen = med["schedule"]
        if med["deletedAt"] is None and regimen is not None and regimen["stoppedAt"] is None:
            result.setdefault(regimen["revisitDate"], []).append(med)
    return result


def build_store_schedule(store):
    validate_store(store)
    occurrences, events = [], []
    for key, rows in _groups(store).items():
        at = parse_instant(rows[0][1]["at"])
        label = time_label(f"{at:%H:%M}")
        states = [d["state"] for _, d in rows]
        prefix = "✅ " if all(s == "TAKEN" for s in states) else "일부 확인 · " if "TAKEN" in states else ""
        details = []
        for _, record in rows:
            suffix = {"TAKEN": " · 복용 확인", "SKIPPED": " · 건너뜀", "MISSED": " · 확인 없음"}.get(record["state"], "")
            details.append("- " + record["line"] + suffix)
        occurrences.append({"key": key, "at": iso(at), "label": label,
                            "lines": [d["line"] for _, d in rows],
                            "doses": [{"doseId": d_id, "medicineId": d["medicineId"], "name": d["name"], "state": d["state"]} for d_id, d in rows]})
        events.append({"key": key, "summary": f"{prefix}약 먹을 시간 · {label} {at:%H:%M}",
                       "start": iso(at), "end": iso(at + timedelta(minutes=10)),
                       "description": "\n".join([*details, NOTICE, f"key: {key}"])})
    for day, medicines in sorted(_revisits(store).items()):
        key = "revisit|" + day
        lines = [f"- {m['name']} · 종료일 {m['schedule']['endDate']}" for m in sorted(medicines, key=lambda m: m["id"])]
        events.append({"key": key, "summary": "병원 다시 가기 · 약 떨어지기 3일 전", "allDayDate": day,
                       "description": "\n".join([*lines, NOTICE, "key: " + key])})
    return {"schemaVersion": 2, "revision": store["revision"], "occurrences": occurrences,
            "revisitDates": sorted(_revisits(store)), "calendarEvents": events}


def _past_event(event, at):
    if "allDayDate" in event:
        return parse_date(event["allDayDate"]) < at.date()
    return parse_instant(event["start"]) < at


def refresh_calendar(store, at, extra_events=None):
    desired = {event["key"]: event for event in build_store_schedule(store)["calendarEvents"]}
    for event in extra_events or []:
        if event["key"] in desired:
            raise ToolError("INVALID_INPUT", "캘린더 일정 key가 중복됩니다.")
        desired[event["key"]] = event
    calendar = store["calendar"]
    for key, applied in calendar["applied"].items():
        if (key not in desired and _past_event(applied["event"], at)
                and calendar["pending"].get(key, {}).get("operation") != "DELETE"):
            desired[key] = applied["event"]
    pending = {}
    for key, event in desired.items():
        applied = calendar["applied"].get(key)
        content_hash = _hash(event)
        if applied is not None and applied["contentHash"] == content_hash:
            continue
        job = {"key": key, "operation": "UPSERT", "eventId": applied["eventId"] if applied else None,
               "event": deepcopy(event), "contentHash": content_hash}
        job["fingerprint"] = _hash(job)
        pending[key] = job
    # An unacknowledged create may already exist in Google Calendar if its
    # response was lost. Keep a deletion by key until absence is confirmed.
    possible = {key: job.get("eventId") for key, job in calendar["pending"].items()}
    possible.update({key: entry["eventId"] for key, entry in calendar["applied"].items()})
    for key, event_id in possible.items():
        if key not in desired:
            job = {"key": key, "operation": "DELETE", "eventId": event_id, "event": None}
            job["fingerprint"] = _hash(job)
            pending[key] = job
    calendar["pending"] = pending


def calendar_plan(store):
    return {"changes": [deepcopy(store["calendar"]["pending"][key]) for key in sorted(store["calendar"]["pending"])]}


def acknowledge_calendar(store, key, fingerprint, event_id=None):
    calendar = store["calendar"]
    job = calendar["pending"].get(key)
    if job is None and calendar["acks"].get(key) == fingerprint:
        return {"acknowledged": key, "replayed": True}
    if job is None or job["fingerprint"] != fingerprint:
        raise ToolError("STALE_CALENDAR_CHANGE", "현재 캘린더 작업과 다른 확인입니다. 작업 목록을 다시 읽어 주세요.")
    if job["operation"] == "UPSERT":
        if not isinstance(event_id, str) or not event_id.strip():
            raise ToolError("INVALID_INPUT", "실제 반영한 event ID가 필요합니다.")
        calendar["applied"][key] = {"eventId": event_id, "contentHash": job["contentHash"], "event": deepcopy(job["event"])}
    else:
        calendar["applied"].pop(key, None)
    calendar["acks"][key] = fingerprint
    calendar["pending"].pop(key, None)
    return {"acknowledged": key, "replayed": False}


def store_due(store, now, voice):
    validate_voice(voice)
    now = now.astimezone(SEOUL)
    send = []
    for key, rows in _groups(store).items():
        pending = [(d_id, d) for d_id, d in rows if d["state"] == "PENDING"]
        if not pending:
            continue
        at = parse_instant(pending[0][1]["at"])
        phase = phase_at(at, now)
        if phase == 0:
            continue
        step = 4 if phase == "MISSED" else phase
        entry = store["reminders"].setdefault(key, {"sentStep": 0})
        for _, record in pending:
            record["sentStep"] = max(record["sentStep"], step)
            if phase == "MISSED":
                record["state"] = "MISSED"
        if step <= entry["sentStep"]:
            continue
        entry["sentStep"] = step
        started = at + timedelta(minutes={1: 0, 2: 10, 3: 30, 4: 90}[step])
        if not is_quiet(now) and not is_quiet(started):
            send.append({"key": key, "step": step, "medicineIds": [d["medicineId"] for _, d in pending],
                         "say": voice["missed" if step == 4 else f"step{step}"]})
    for day, medicines in sorted(_revisits(store).items()):
        if now.date().isoformat() != day or now.hour < 9 or is_quiet(now):
            continue
        key = "revisit|" + day
        entry = store["reminders"].setdefault(key, {"sentStep": 0})
        if entry["sentStep"] < 1:
            entry["sentStep"] = 1
            send.append({"key": key, "step": 1, "medicineIds": sorted(m["id"] for m in medicines), "say": voice["revisit"]})
    return {"now": iso(now), "send": send}


def store_mark(store, at, source=None, key=None, medicine_ids=None, skip=False, voice=None):
    if not skip and source not in ("chat", "desk"):
        raise ToolError("INVALID_INPUT", "source: chat 또는 desk가 필요합니다.")
    if not skip:
        validate_voice(voice)
    selected = set(medicine_ids or [])
    for medicine_id in selected:
        _medicine(store, medicine_id, include_deleted=True)
    groups = _groups(store)
    if key in store["doses"]:
        rows = [(key, store["doses"][key])]
        group_key = "|".join(key.split("|")[-2:])
    elif key is not None:
        if key not in groups:
            raise ToolError("UNKNOWN_OCCURRENCE", "해당 회차를 찾을 수 없습니다.")
        rows, group_key = groups[key], key
    else:
        eligible = []
        for candidate, members in groups.items():
            scheduled = parse_instant(members[0][1]["at"])
            if (any(d["state"] == "PENDING" and (not selected or d["medicineId"] in selected) for _, d in members)
                    and -3600 <= (at - scheduled).total_seconds() <= 5400):
                eligible.append((abs((at - scheduled).total_seconds()), candidate))
        if not eligible:
            return {"skipped" if skip else "marked": None}
        group_key = min(eligible)[1]
        rows = groups[group_key]
    rows = [(d_id, d) for d_id, d in rows if not selected or d["medicineId"] in selected]
    if not rows:
        raise ToolError("MEDICINE_NOT_IN_OCCURRENCE", "선택한 약이 해당 회차에 없습니다.")
    processed = []
    for dose_id, record in rows:
        if skip:
            if record["state"] == "TAKEN":
                continue
            if record["state"] != "SKIPPED":
                record.update(state="SKIPPED", skipReason="USER_SKIP")
        elif record["state"] != "TAKEN":
            record.update(state="TAKEN", takenAt=iso(at), source=source)
        processed.append(dose_id)
    if not processed:
        return {"skipped" if skip else "marked": None}
    result = {"skipped" if skip else "marked": group_key, "doseIds": processed,
              "medicineIds": [store["doses"][d]["medicineId"] for d in processed], "state": "SKIPPED" if skip else "TAKEN"}
    if not skip:
        result["say"] = voice["taken"]
    return result


def _migrate_legacy(store, directory, at):
    source = directory / "prescription.json"
    if not source.is_file():
        if (directory / "schedule.json").is_file() or (directory / "doses.json").is_file():
            raise ToolError("MIGRATION_CONFLICT", "기존 일정·복용 기록에 대응하는 prescription.json이 필요합니다.")
        return False
    with state_lock(directory / "doses.json"):
        prescription = read_json(source)
        doses_path, schedule_path = directory / "doses.json", directory / "schedule.json"
        old_doses = read_json(doses_path) if doses_path.exists() else {}
        if not isinstance(old_doses, dict):
            raise ToolError("MIGRATION_CONFLICT", "기존 복용 기록 형식을 확인해 주세요.")
        slots_path = directory / "slots.json"
        legacy_slots = read_json(slots_path) if slots_path.is_file() else None
        expected = build_schedule(prescription, legacy_slots, today=at.date())
        if old_doses:
            if not schedule_path.exists():
                raise ToolError("MIGRATION_CONFLICT", "기존 복용 기록의 일정 원본이 필요합니다.")
            old_schedule = read_json(schedule_path)
            if not isinstance(old_schedule, dict):
                raise ToolError("MIGRATION_CONFLICT", "기존 일정의 형식을 확인해 주세요.")
            old_rows = old_schedule.get("occurrences")
            def signature(rows):
                if not isinstance(rows, list) or any(not isinstance(o, dict) for o in rows):
                    return None
                return [(o.get("key"), o.get("at"), o.get("lines")) for o in rows]
            if (old_schedule.get("confirmedAt") != expected["confirmedAt"]
                    or signature(old_rows) != signature(expected["occurrences"])):
                raise ToolError("MIGRATION_CONFLICT", "기존 처방·일정·복용 기록의 출처가 다릅니다.")
        added = add_medicines(store, prescription, at, request_id="legacy:" + _hash(prescription), slots=legacy_slots)
        known = set(_groups(store))
        for key, entry in old_doses.items():
            if key.startswith("revisit|"):
                if not isinstance(entry, dict) or type(entry.get("sentStep")) is not int or not 0 <= entry["sentStep"] <= 1:
                    raise ToolError("MIGRATION_CONFLICT", "기존 재방문 기록을 확인해 주세요.")
                store["reminders"][key] = {"sentStep": entry["sentStep"]}
                continue
            if (key not in known or not isinstance(entry, dict) or not isinstance(entry.get("state"), str)
                    or entry["state"] not in STATES
                    or type(entry.get("sentStep")) is not int or not 0 <= entry["sentStep"] <= 4):
                raise ToolError("MIGRATION_CONFLICT", "어느 약의 기록인지 확인할 수 없는 기존 회차가 있습니다.")
            if entry["state"] == "TAKEN":
                parse_instant(entry.get("takenAt"), "legacy.takenAt")
                if entry.get("source") not in ("chat", "desk"):
                    raise ToolError("MIGRATION_CONFLICT", "기존 복용 확인 출처가 없습니다.")
            store["reminders"][key] = {"sentStep": entry["sentStep"]}
            for _, record in _groups(store)[key]:
                record.update({k: entry.get(k) for k in ("state", "sentStep", "takenAt", "source")})
        store["migration"] = {"importedAt": iso(at), "medicineIds": added["added"], "sourceFingerprint": _hash(prescription)}
    return True


def transact(path, action, at=None, expected_revision=None, include_changes=True):
    at = (at or datetime.now(SEOUL)).astimezone(SEOUL)
    path = Path(path).resolve()
    with state_lock(path):
        store = validate_store(read_json(path)) if path.exists() else empty_store()
        before = deepcopy(store)
        if expected_revision is not None and expected_revision != store["revision"]:
            raise ToolError("REVISION_CONFLICT", "목록이 바뀌었습니다. 최신 목록을 확인해 주세요.")
        if not path.exists():
            _migrate_legacy(store, path.parent, at)
        refresh_calendar(store, at)
        result = action(store)
        validate_store(store)
        refresh_calendar(store, at)
        if store != before:
            store["revision"] = before["revision"] + 1
            atomic_write_json(path, store)
        result["revision"] = store["revision"]
        if include_changes:
            result["calendarChanges"] = calendar_plan(store)["changes"]
        return result
