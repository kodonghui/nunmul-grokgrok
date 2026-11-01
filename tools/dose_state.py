"""Calculate due reminders and record a user's taken/skip confirmation."""

from datetime import datetime, timedelta
from pathlib import Path

if __package__:
    from .common import (JsonArgumentParser, SEOUL, ToolError, atomic_write_json,
                         iso, parse_date, parse_instant, read_json, run_cli, state_lock)
else:
    from common import (JsonArgumentParser, SEOUL, ToolError, atomic_write_json,
                        iso, parse_date, parse_instant, read_json, run_cli, state_lock)

if __package__:
    from .dose_rules import VOICE_PATH, is_quiet, phase_at, validate_voice as _voice
else:
    from dose_rules import VOICE_PATH, is_quiet, phase_at, validate_voice as _voice


STATES = {"PENDING", "TAKEN", "MISSED", "SKIPPED"}


def _initialize(schedule, doses):
    if not isinstance(schedule, dict) or not isinstance(doses, dict):
        raise ToolError("INVALID_INPUT", "일정과 복용 기록 객체가 필요합니다.")
    confirmed = parse_instant(schedule.get("confirmedAt"), "confirmedAt")
    revisit = parse_date(schedule.get("revisitDate"), "revisitDate")
    occurrences = schedule.get("occurrences")
    if not isinstance(occurrences, list):
        raise ToolError("INVALID_INPUT", "occurrences: 회차 목록이 필요합니다.")
    ordered = []
    keys = set()
    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            raise ToolError("INVALID_INPUT", "회차 형식을 확인해 주세요.")
        at = parse_instant(occurrence.get("at"), "occurrence.at")
        key = f"{at.date().isoformat()}|{at:%H:%M}"
        if occurrence.get("key") != key or at.second or at.microsecond or key in keys:
            raise ToolError("INVALID_INPUT", "회차 key와 시각 또는 중복을 확인해 주세요.")
        keys.add(key)
        if key not in doses:
            doses[key] = {"state": "SKIPPED" if at < confirmed else "PENDING",
                          "sentStep": 0, "takenAt": None, "source": None}
        entry = doses[key]
        if (not isinstance(entry, dict) or not isinstance(entry.get("state"), str) or entry.get("state") not in STATES
                or type(entry.get("sentStep")) is not int or not 0 <= entry["sentStep"] <= 4
                or entry.get("source") not in (None, "chat", "desk")):
            raise ToolError("INVALID_STATE", "저장된 복용 상태를 확인해 주세요.")
        if entry.get("takenAt") is not None:
            parse_instant(entry["takenAt"], "takenAt")
        if entry["state"] == "TAKEN" and (entry.get("takenAt") is None or entry.get("source") is None):
            raise ToolError("INVALID_STATE", "복용 확인 시각과 출처가 필요합니다.")
        if at < confirmed and entry["state"] == "PENDING":
            entry["state"] = "SKIPPED"
        ordered.append((at, key))
    return sorted(ordered), revisit


def due(schedule, doses, now, voice):
    voice = _voice(voice)
    ordered, revisit = _initialize(schedule, doses)
    send = []
    for at, key in ordered:
        entry = doses[key]
        if entry["state"] != "PENDING":
            continue
        phase = phase_at(at, now)
        if phase == "MISSED":
            entry["state"] = "MISSED"
            entry["sentStep"] = 4
            if not is_quiet(now) and not is_quiet(at + timedelta(minutes=90)):
                send.append({"key": key, "step": 4, "say": voice["missed"]})
        elif phase > entry["sentStep"]:
            entry["sentStep"] = phase
            started = at + timedelta(minutes={1: 0, 2: 10, 3: 30}[phase])
            if not is_quiet(now) and not is_quiet(started):
                send.append({"key": key, "step": phase, "say": voice[f"step{phase}"]})
    if now.astimezone(SEOUL).date() == revisit and now.astimezone(SEOUL).hour >= 9 and not is_quiet(now):
        key = f"revisit|{revisit.isoformat()}"
        entry = doses.setdefault(key, {"sentStep": 0})
        if not isinstance(entry, dict) or type(entry.get("sentStep")) is not int:
            raise ToolError("INVALID_STATE", "저장된 재방문 알림 상태를 확인해 주세요.")
        if entry["sentStep"] < 1:
            send.append({"key": key, "step": 1, "say": voice["revisit"]})
            entry["sentStep"] = 1
    return {"now": iso(now), "send": send}


def _target(ordered, doses, key, at):
    if key is not None:
        if key not in {candidate for _, candidate in ordered}:
            raise ToolError("UNKNOWN_OCCURRENCE", "지정한 회차가 일정에 없습니다.")
        return key
    eligible = [(abs((at - scheduled).total_seconds()), scheduled, candidate)
                for scheduled, candidate in ordered
                if doses[candidate]["state"] == "PENDING"
                and -3600 <= (at - scheduled).total_seconds() <= 5400]
    return min(eligible)[2] if eligible else None


def mark(schedule, doses, at, source, voice, key=None):
    if source not in ("chat", "desk"):
        raise ToolError("INVALID_INPUT", "source: chat 또는 desk가 필요합니다.")
    voice = _voice(voice)
    ordered, _ = _initialize(schedule, doses)
    key = _target(ordered, doses, key, at)
    if key is None:
        return {"marked": None}
    entry = doses[key]
    if entry["state"] != "TAKEN":
        entry.update(state="TAKEN", takenAt=iso(at), source=source)
    return {"marked": key, "state": "TAKEN", "takenAt": entry["takenAt"],
            "source": entry["source"], "say": voice["taken"]}


def skip(schedule, doses, at, key=None):
    ordered, _ = _initialize(schedule, doses)
    key = _target(ordered, doses, key, at)
    if key is None or doses[key]["state"] == "TAKEN":
        return {"skipped": None}
    doses[key]["state"] = "SKIPPED"
    return {"skipped": key, "state": "SKIPPED"}


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("due", "mark", "skip"):
        sub = commands.add_parser(command)
        sub.add_argument("--schedule", type=Path)
        sub.add_argument("--doses", type=Path)
        sub.add_argument("--store", type=Path)
        sub.add_argument("--medicine-id", action="append", default=[])
        if command == "due":
            sub.add_argument("--now", required=True)
        else:
            sub.add_argument("key", nargs="?")
            sub.add_argument("--at")
        if command == "mark":
            sub.add_argument("--source", required=True, choices=("chat", "desk"))
    args = parser.parse_args(argv)
    if args.command == "due" and args.medicine_id:
        parser.error("--medicine-id는 mark와 skip에서 사용해 주세요.")
    value = args.now if args.command == "due" else args.at
    now = parse_instant(value, "now" if args.command == "due" else "at") if value is not None else datetime.now(SEOUL)
    if args.store is not None and (args.schedule is not None or args.doses is not None):
        parser.error("--store와 v1 상태 파일을 함께 지정할 수 없습니다.")
    use_store = args.store is not None or (args.schedule is None and args.doses is None)
    if use_store:
        if __package__:
            from .med_store import DEFAULT_STORE, store_due, store_mark, transact
        else:
            from med_store import DEFAULT_STORE, store_due, store_mark, transact
        def action(store):
            if args.command == "due":
                return store_due(store, now, read_json(VOICE_PATH))
            return store_mark(store, now, source=args.source if args.command == "mark" else None,
                              key=args.key, medicine_ids=args.medicine_id, skip=args.command == "skip",
                              voice=read_json(VOICE_PATH) if args.command == "mark" else None)
        return transact(args.store or DEFAULT_STORE, action, now)
    if args.medicine_id:
        parser.error("--medicine-id는 누적 원장 모드에서 사용해 주세요.")
    args.schedule = args.schedule or Path("state/schedule.json")
    args.doses = args.doses or Path("state/doses.json")
    doses_path = args.doses.resolve()
    with state_lock(doses_path):
        schedule = read_json(args.schedule)
        doses = read_json(doses_path) if doses_path.exists() else {}
        if args.command == "due":
            result = due(schedule, doses, now, read_json(VOICE_PATH))
        elif args.command == "mark":
            result = mark(schedule, doses, now, args.source, read_json(VOICE_PATH), args.key)
        else:
            result = skip(schedule, doses, now, args.key)
        atomic_write_json(doses_path, doses)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
