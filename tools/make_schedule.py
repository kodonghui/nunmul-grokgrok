"""Turn a user-confirmed prescription into occurrences and calendar data."""

from datetime import date, datetime, time, timedelta
from pathlib import Path

if __package__:
    from .common import (JsonArgumentParser, SEOUL, ToolError, atomic_write_json,
                         iso, parse_date, parse_hhmm, parse_instant, read_json, run_cli)
else:
    from common import (JsonArgumentParser, SEOUL, ToolError, atomic_write_json,
                        iso, parse_date, parse_hhmm, parse_instant, read_json, run_cli)


DEFAULT_SLOTS = {"MORNING": "07:00", "NOON": "11:00", "EVENING": "17:00", "BEDTIME": "22:00"}
DAILY_PERIODS = {1: ["MORNING"], 2: ["MORNING", "EVENING"], 3: ["MORNING", "NOON", "EVENING"]}
NOTICE = "확인용 정보 · 약은 약사·의사와 상담하세요"


def time_label(hhmm):
    hour = int(hhmm[:2])
    if 4 <= hour < 11:
        return "아침"
    if 11 <= hour < 16:
        return "점심"
    if 16 <= hour < 22:
        return "저녁"
    return "밤"


def _slots(overrides):
    result = DEFAULT_SLOTS.copy()
    if overrides is not None:
        if not isinstance(overrides, dict) or any(key not in result for key in overrides):
            raise ToolError("INVALID_INPUT", "slots: 지원하는 복용 때와 시각이 필요합니다.")
        for period, value in overrides.items():
            result[period] = parse_hhmm(value, f"slots.{period}")
    return result


def _times(item, slots):
    periods = item.get("dosePeriods")
    count = item.get("dailyCount")
    if periods is not None:
        if (not isinstance(periods, list) or not 1 <= len(periods) <= 3
                or any(not isinstance(p, str) or p not in slots for p in periods)
                or len(set(periods)) != len(periods)):
            raise ToolError("INVALID_INPUT", "dosePeriods: 서로 다른 복용 때 1~3개가 필요합니다.")
        if count is not None and (type(count) is not int or count != len(periods)):
            raise ToolError("INVALID_INPUT", "dailyCount와 dosePeriods 개수가 다릅니다.")
    else:
        if type(count) is not int or count not in DAILY_PERIODS:
            raise ToolError("INVALID_INPUT", "dailyCount: 하루 횟수 1~3이 필요합니다.")
        periods = DAILY_PERIODS[count]
    times = sorted(slots[period] for period in periods)
    if len(set(times)) != len(times):
        raise ToolError("INVALID_INPUT", "약마다 복용 시각은 서로 달라야 합니다.")
    return times


def build_schedule(prescription, slots=None, today=None):
    if not isinstance(prescription, dict):
        raise ToolError("INVALID_INPUT", "확인된 처방 객체가 필요합니다.")
    start = parse_date(prescription.get("startDate"), "startDate")
    confirmed = parse_instant(prescription.get("confirmedAt"), "confirmedAt")
    today = today if today is not None else datetime.now(SEOUL).date()
    if not isinstance(today, date) or isinstance(today, datetime):
        raise ToolError("INVALID_INPUT", "today: 날짜가 필요합니다.")
    items = prescription.get("items")
    if not isinstance(items, list) or not items:
        raise ToolError("INVALID_INPUT", "items: 확인된 약이 한 개 이상 필요합니다.")
    slots = _slots(slots)
    occurrences = {}
    ends = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ToolError("INVALID_INPUT", "name: 확인된 약 이름이 필요합니다.")
        duration = item.get("durationDays")
        if type(duration) is not int or duration < 1:
            raise ToolError("INVALID_INPUT", "durationDays: 1 이상의 일수가 필요합니다.")
        dose = item.get("dosePerTime")
        if (isinstance(dose, bool) or not isinstance(dose, (int, float))
                or not 0 < dose <= 10 or dose * 2 % 1 != 0):
            raise ToolError("INVALID_INPUT", "dosePerTime: 0보다 크고 10 이하인 0.5 단위 숫자가 필요합니다.")
        timing = item.get("timing")
        if timing is not None and not isinstance(timing, str):
            raise ToolError("INVALID_INPUT", "timing: 문구 또는 null이 필요합니다.")
        try:
            end = start + timedelta(days=duration - 1)
        except (OverflowError, ValueError):
            raise ToolError("INVALID_INPUT", "durationDays: 계산할 수 있는 날짜 범위를 넘었습니다.") from None
        ends.append(end)
        times = _times(item, slots)
        line = f"{item['name'].strip()} 1회 {dose:g}개"
        if timing and timing.strip():
            line += f" · {timing.strip()}"
        for offset in range(duration):
            day = start + timedelta(days=offset)
            for hhmm in times:
                key = f"{day.isoformat()}|{hhmm}"
                at = datetime.combine(day, time.fromisoformat(hhmm), SEOUL)
                occurrence = occurrences.setdefault(key, {
                    "key": key, "at": iso(at), "label": time_label(hhmm), "lines": [],
                })
                occurrence["lines"].append(line)
    ordered = [occurrences[key] for key in sorted(occurrences)]
    end = max(ends)
    revisit = max(date.fromordinal(max(1, end.toordinal() - 3)), today)
    events = []
    for occurrence in ordered:
        at = parse_instant(occurrence["at"])
        events.append({
            "key": occurrence["key"],
            "summary": f"약 먹을 시간 · {occurrence['label']} {occurrence['key'].split('|')[1]}",
            "start": occurrence["at"], "end": iso(at + timedelta(minutes=10)),
            "description": "\n".join([*(f"- {line}" for line in occurrence["lines"]),
                                       NOTICE, f"key: {occurrence['key']}"]),
        })
    revisit_key = f"revisit|{revisit.isoformat()}"
    events.append({
        "key": revisit_key, "summary": "병원 다시 가기 · 약 떨어지기 3일 전",
        "allDayDate": revisit.isoformat(), "description": f"{NOTICE}\nkey: {revisit_key}",
    })
    return {"startDate": start.isoformat(), "endDate": end.isoformat(),
            "revisitDate": revisit.isoformat(), "confirmedAt": iso(confirmed),
            "occurrences": ordered, "calendarEvents": events}


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--slots", type=Path)
    parser.add_argument("--today")
    args = parser.parse_args(argv)
    if (args.input is None) == (args.store is None):
        parser.error("--in 또는 --store 중 하나를 지정해 주세요.")
    if args.store is not None:
        if args.slots is not None or args.today is not None:
            parser.error("원장의 일정은 my_meds.py로 변경해 주세요.")
        if args.store.resolve() == args.out.resolve():
            parser.error("조회 결과의 출력 경로와 원장 경로는 달라야 합니다.")
        if __package__:
            from .med_store import build_store_schedule, transact
        else:
            from med_store import build_store_schedule, transact
        schedule = transact(args.store, build_store_schedule, include_changes=False)
        atomic_write_json(args.out, schedule)
        return schedule
    slots_path = args.slots
    if slots_path is None and Path("state/slots.json").is_file():
        slots_path = Path("state/slots.json")
    slots = read_json(slots_path) if slots_path is not None else None
    today = parse_date(args.today, "today") if args.today is not None else None
    schedule = build_schedule(read_json(args.input), slots, today)
    atomic_write_json(args.out, schedule)
    return schedule


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
