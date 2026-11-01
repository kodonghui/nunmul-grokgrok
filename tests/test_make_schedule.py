from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.common import ToolError, atomic_write_json, read_json
from tools.make_schedule import build_schedule, time_label


ROOT = Path(__file__).resolve().parents[1]


def prescription(**changes):
    item = {"readNo": 1, "itemSeq": "100001", "name": "가상약정5밀리그램",
            "dosePerTime": 1, "dailyCount": 2, "dosePeriods": None,
            "durationDays": 5, "timing": "식후 30분"}
    item.update(changes)
    return {"startDate": "2026-11-01", "confirmedAt": "2026-11-01T13:00:00+09:00", "items": [item]}


class ScheduleTests(unittest.TestCase):
    def build(self, data=None, slots=None, today=date(2026, 11, 1)):
        return build_schedule(data if data is not None else prescription(), slots, today)

    def test_five_days_twice_daily_inclusive(self):
        result = self.build()
        self.assertEqual(len(result["occurrences"]), 10)
        self.assertEqual(result["endDate"], "2026-11-05")
        self.assertEqual(result["revisitDate"], "2026-11-02")
        self.assertEqual([o["key"] for o in result["occurrences"][:2]],
                         ["2026-11-01|07:00", "2026-11-01|17:00"])
        self.assertEqual(len(result["calendarEvents"]), 11)

    def test_calendar_event_contract(self):
        result = self.build()
        evening = result["calendarEvents"][1]
        self.assertEqual(evening["summary"], "약 먹을 시간 · 저녁 17:00")
        self.assertEqual(evening["start"], "2026-11-01T17:00:00+09:00")
        self.assertEqual(evening["end"], "2026-11-01T17:10:00+09:00")
        self.assertEqual(evening["description"].splitlines(), [
            "- 가상약정5밀리그램 1회 1개 · 식후 30분",
            "확인용 정보 · 약은 약사·의사와 상담하세요", "key: 2026-11-01|17:00"])
        revisit = result["calendarEvents"][-1]
        self.assertEqual(revisit["key"], "revisit|2026-11-02")
        self.assertEqual(revisit["allDayDate"], "2026-11-02")
        self.assertNotIn("start", revisit)

    def test_daily_count_defaults(self):
        for count, expected in [(1, ["07:00"]), (2, ["07:00", "17:00"]),
                                (3, ["07:00", "11:00", "17:00"])]:
            with self.subTest(count=count):
                result = self.build(prescription(dailyCount=count, durationDays=1))
                self.assertEqual([o["key"].split("|")[1] for o in result["occurrences"]], expected)

    def test_explicit_bedtime_and_partial_slots(self):
        result = self.build(prescription(dailyCount=None, dosePeriods=["BEDTIME"], durationDays=1),
                            {"BEDTIME": "21:30"})
        self.assertEqual(result["occurrences"][0]["key"], "2026-11-01|21:30")

    def test_user_slots_are_sorted_and_timing_does_not_shift_them(self):
        result = self.build(prescription(dosePeriods=["EVENING", "MORNING"], durationDays=1),
                            {"MORNING": "08:30", "EVENING": "18:45"})
        self.assertEqual([o["key"] for o in result["occurrences"]],
                         ["2026-11-01|08:30", "2026-11-01|18:45"])

    def test_same_time_groups_medicines_with_different_durations(self):
        data = prescription(dosePerTime=0.5, timing=None)
        second = deepcopy(data["items"][0])
        second.update(readNo=2, name="다른가상약정", durationDays=2)
        data["items"].append(second)
        result = self.build(data)
        self.assertEqual(len(result["occurrences"]), 10)
        self.assertEqual(len(result["occurrences"][0]["lines"]), 2)
        self.assertEqual(result["occurrences"][0]["lines"][0], "가상약정5밀리그램 1회 0.5개")
        self.assertEqual(len(result["occurrences"][-1]["lines"]), 1)
        self.assertEqual(result["revisitDate"], "2026-11-02")

    def test_revisit_is_clamped_to_today(self):
        self.assertEqual(self.build(today=date(2026, 11, 10))["revisitDate"], "2026-11-10")

    def test_invalid_or_missing_duration_is_rejected(self):
        for value in (None, 0, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ToolError):
                self.build(prescription(durationDays=value))

    def test_invalid_doses_are_rejected(self):
        for value in (None, 0, -1, 0.25, 10.5, True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ToolError):
                self.build(prescription(dosePerTime=value))

    def test_invalid_periods_counts_and_duplicate_times_are_rejected(self):
        for changes in ({"dailyCount": 4}, {"dailyCount": True}, {"dosePeriods": []},
                        {"dosePeriods": ["MORNING", "MORNING"]},
                        {"dosePeriods": ["UNKNOWN"]}, {"dosePeriods": ["MORNING"]}):
            with self.subTest(changes=changes), self.assertRaises(ToolError):
                self.build(prescription(**changes))
        with self.assertRaises(ToolError):
            self.build(slots={"MORNING": "17:00"})

    def test_invalid_slots_are_rejected(self):
        for slots in ({"MORNING": "24:00"}, {"NOON": "9:00"}, {"OTHER": "12:00"}, []):
            with self.subTest(slots=slots), self.assertRaises(ToolError):
                self.build(slots=slots)

    def test_labels_at_every_boundary(self):
        for hhmm, label in [("03:59", "밤"), ("04:00", "아침"), ("10:59", "아침"),
                            ("11:00", "점심"), ("15:59", "점심"), ("16:00", "저녁"),
                            ("21:59", "저녁"), ("22:00", "밤")]:
            self.assertEqual(time_label(hhmm), label)

    def test_event_can_end_on_next_day(self):
        result = self.build(prescription(dailyCount=1, dosePeriods=["BEDTIME"], durationDays=1),
                            {"BEDTIME": "23:55"})
        self.assertEqual(result["calendarEvents"][0]["end"], "2026-11-02T00:05:00+09:00")

    def test_invalid_dates_naive_confirmation_and_empty_items(self):
        for field, value in [("startDate", "2026-02-30"), ("startDate", "20261101"),
                             ("confirmedAt", "2026-11-01T13:00:00"), ("items", [])]:
            data = prescription()
            data[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ToolError):
                self.build(data)

    def test_timezone_is_normalized(self):
        data = prescription()
        data["confirmedAt"] = "2026-11-01T04:00:00Z"
        self.assertEqual(self.build(data)["confirmedAt"], "2026-11-01T13:00:00+09:00")

    def test_personal_fields_do_not_enter_generated_schedule(self):
        data = prescription()
        data["patientName"] = "합성 테스트 입력"
        self.assertNotIn("patientName", self.build(data))


class ScheduleFileTests(unittest.TestCase):
    def run_tool(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / "tools/make_schedule.py"), *map(str, args)],
                              cwd=cwd or ROOT, capture_output=True, text=True, encoding="utf-8")

    def test_cli_writes_exactly_one_json_and_the_same_output_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "처방.json", root / "state/일정.json"
            source.write_text(json.dumps(prescription(), ensure_ascii=False), encoding="utf-8")
            process = self.run_tool("--in", source, "--out", target, "--today", "2026-11-01", cwd=root)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertEqual(len(process.stdout.splitlines()), 1)
            self.assertEqual(json.loads(process.stdout), read_json(target))
            self.assertEqual(process.stderr, "")

    def test_cli_automatically_uses_state_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write_json(root / "input.json", prescription())
            atomic_write_json(root / "state/slots.json", {"MORNING": "08:30"})
            process = self.run_tool("--in", root / "input.json", "--out", root / "state/out.json", cwd=root)
            self.assertEqual(process.returncode, 0, process.stdout)
            self.assertEqual(json.loads(process.stdout)["occurrences"][0]["key"], "2026-11-01|08:30")

    def test_cli_validation_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "input.json", root / "out.json"
            atomic_write_json(source, prescription(durationDays=None))
            atomic_write_json(target, {"previous": True})
            process = self.run_tool("--in", source, "--out", target)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(json.loads(process.stdout)["error"]["code"], "INVALID_INPUT")
            self.assertEqual(read_json(target), {"previous": True})

    def test_argument_failure_is_json_and_nonzero(self):
        process = self.run_tool()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(process.stderr, "")

    def test_failed_atomic_replace_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "out.json"
            atomic_write_json(target, {"previous": True})
            with patch("tools.common.os.replace", side_effect=OSError), self.assertRaises(ToolError):
                atomic_write_json(target, {"replacement": True})
            self.assertEqual(read_json(target), {"previous": True})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_non_finite_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text('{"dose": NaN}', encoding="utf-8")
            with self.assertRaises(ToolError):
                read_json(source)


if __name__ == "__main__":
    unittest.main()
