from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.common import ToolError, atomic_write_json, parse_instant, read_json
from tools.dose_state import VOICE_PATH, due, mark, phase_at, skip


ROOT = Path(__file__).resolve().parents[1]
VOICE = read_json(VOICE_PATH)
KEY = "2026-11-01|17:00"


def instant(hhmm, day="2026-11-01"):
    return parse_instant(f"{day}T{hhmm}:00+09:00")


def schedule(hhmm="17:00", confirmed="13:00"):
    return {"confirmedAt": f"2026-11-01T{confirmed}:00+09:00", "revisitDate": "2026-11-02",
            "occurrences": [{"key": f"2026-11-01|{hhmm}", "at": f"2026-11-01T{hhmm}:00+09:00"}]}


class DoseTests(unittest.TestCase):
    def test_all_phase_boundaries(self):
        at = instant("17:00")
        for seconds, phase in [(-1, 0), (0, 1), (599, 1), (600, 2), (1799, 2),
                               (1800, 3), (5399, 3), (5400, "MISSED")]:
            with self.subTest(seconds=seconds):
                self.assertEqual(phase_at(at, at + timedelta(seconds=seconds)), phase)

    def test_sequence_sends_each_stage_and_missed_once(self):
        doses = {}
        for hhmm, step in [("17:05", 1), ("17:12", 2), ("17:31", 3), ("18:31", 4)]:
            result = due(schedule(), doses, instant(hhmm), VOICE)
            self.assertEqual([s["step"] for s in result["send"]], [step])
        self.assertEqual(doses[KEY]["state"], "MISSED")
        self.assertEqual(due(schedule(), doses, instant("19:00"), VOICE)["send"], [])

    def test_late_first_poll_only_sends_current_phase_and_is_idempotent(self):
        doses = {}
        first = due(schedule(), doses, instant("17:12"), VOICE)
        self.assertEqual([s["step"] for s in first["send"]], [2])
        self.assertEqual(due(schedule(), doses, instant("17:12"), VOICE)["send"], [])

    def test_past_before_registration_is_skipped(self):
        doses = {}
        self.assertEqual(due(schedule("07:00"), doses, instant("17:12"), VOICE)["send"], [])
        self.assertEqual(doses["2026-11-01|07:00"]["state"], "SKIPPED")

    def test_confirmation_at_exact_occurrence_does_not_skip_it(self):
        self.assertEqual(len(due(schedule(confirmed="17:00"), {}, instant("17:00"), VOICE)["send"]), 1)

    def test_future_is_pending_without_message(self):
        doses = {}
        self.assertEqual(due(schedule(), doses, instant("16:59"), VOICE)["send"], [])
        self.assertEqual(doses[KEY]["state"], "PENDING")
        self.assertEqual(doses[KEY]["sentStep"], 0)

    def test_mark_twice_preserves_first_timestamp_source_and_result(self):
        doses = {}
        first = mark(schedule(), doses, instant("17:05"), "chat", VOICE, KEY)
        second = mark(schedule(), doses, instant("17:12"), "desk", VOICE, KEY)
        self.assertEqual(first, second)
        self.assertEqual(doses[KEY]["takenAt"], "2026-11-01T17:05:00+09:00")
        self.assertEqual(due(schedule(), doses, instant("17:31"), VOICE)["send"], [])

    def test_mark_without_key_chooses_nearest_pending(self):
        data = schedule()
        data["occurrences"].append({"key": "2026-11-01|17:30", "at": "2026-11-01T17:30:00+09:00"})
        doses = {}
        self.assertEqual(mark(data, doses, instant("17:25"), "chat", VOICE)["marked"], "2026-11-01|17:30")

    def test_mark_without_eligible_occurrence_returns_null(self):
        self.assertEqual(mark(schedule(), {}, instant("15:59"), "chat", VOICE), {"marked": None})
        self.assertEqual(mark(schedule(), {}, instant("18:31"), "chat", VOICE), {"marked": None})

    def test_automatic_target_window_includes_boundaries(self):
        for at in ("16:00", "18:30"):
            self.assertEqual(mark(schedule(), {}, instant(at), "chat", VOICE)["marked"], KEY)

    def test_skip_is_idempotent_and_stops_reminders(self):
        doses = {}
        first = skip(schedule(), doses, instant("17:05"))
        self.assertEqual(first, skip(schedule(), doses, instant("17:12"), KEY))
        self.assertEqual(doses[KEY]["state"], "SKIPPED")
        self.assertEqual(due(schedule(), doses, instant("17:31"), VOICE)["send"], [])

    def test_skip_does_not_erase_taken_record(self):
        doses = {}
        mark(schedule(), doses, instant("17:05"), "chat", VOICE, KEY)
        self.assertEqual(skip(schedule(), doses, instant("17:10"), KEY), {"skipped": None})
        self.assertEqual(doses[KEY]["state"], "TAKEN")

    def test_explicit_mark_can_record_a_late_confirmation(self):
        doses = {}
        due(schedule(), doses, instant("18:31"), VOICE)
        result = mark(schedule(), doses, instant("18:35"), "chat", VOICE, KEY)
        self.assertEqual(result["state"], "TAKEN")

    def test_unknown_key_does_not_create_a_record(self):
        doses = {}
        with self.assertRaises(ToolError):
            mark(schedule(), doses, instant("17:05"), "chat", VOICE, "unknown")
        self.assertNotIn("unknown", doses)

    def test_nighttime_missed_is_silent_and_never_replayed(self):
        doses = {}
        data = schedule("22:00")
        self.assertEqual(due(data, doses, instant("23:31"), VOICE)["send"], [])
        self.assertEqual(doses["2026-11-01|22:00"]["state"], "MISSED")
        self.assertEqual(due(data, doses, instant("07:00", "2026-11-02"), VOICE)["send"], [])

    def test_first_morning_poll_does_not_replay_nighttime_missed(self):
        self.assertEqual(due(schedule("22:00"), {}, instant("07:00", "2026-11-02"), VOICE)["send"], [])

    def test_quiet_phase_advances_without_sending(self):
        doses = {}
        data = schedule("23:00")
        self.assertEqual(due(data, doses, instant("23:12"), VOICE)["send"], [])
        self.assertEqual(doses["2026-11-01|23:00"]["sentStep"], 2)

    def test_seven_is_allowed_but_a_phase_started_before_seven_is_skipped(self):
        self.assertEqual(len(due(schedule("07:00", "06:00"), {}, instant("07:00"), VOICE)["send"]), 1)
        self.assertEqual(due(schedule("06:55", "06:00"), {}, instant("07:00"), VOICE)["send"], [])

    def test_revisit_after_nine_once_and_only_on_that_date(self):
        doses = {}
        data = {"confirmedAt": "2026-11-01T13:00:00+09:00", "revisitDate": "2026-11-02", "occurrences": []}
        self.assertEqual(due(data, doses, instant("08:59", "2026-11-02"), VOICE)["send"], [])
        result = due(data, doses, instant("09:00", "2026-11-02"), VOICE)
        self.assertEqual(result["send"], [{"key": "revisit|2026-11-02", "step": 1, "say": VOICE["revisit"]}])
        self.assertEqual(due(data, doses, instant("09:00", "2026-11-02"), VOICE)["send"], [])
        self.assertEqual(due(data, {}, instant("23:00", "2026-11-02"), VOICE)["send"], [])
        self.assertEqual(due(data, {}, instant("09:00", "2026-11-03"), VOICE)["send"], [])

    def test_corrupt_state_or_schedule_is_rejected(self):
        for bad in ({"state": "OTHER", "sentStep": 0}, {"state": "PENDING", "sentStep": True}):
            with self.assertRaises(ToolError):
                due(schedule(), {KEY: bad}, instant("17:12"), VOICE)
        data = schedule()
        data["occurrences"][0]["key"] = "2026-11-01|18:00"
        with self.assertRaises(ToolError):
            due(data, {}, instant("17:12"), VOICE)


class DoseCliTests(unittest.TestCase):
    def command(self, root, *args):
        return [sys.executable, str(ROOT / "tools/dose_state.py"), *args,
                "--schedule", str(root / "schedule.json"), "--doses", str(root / "doses.json")]

    def test_due_is_persistent_between_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write_json(root / "schedule.json", schedule())
            outputs = [subprocess.run(self.command(root, "due", "--now", "2026-11-01T17:12:00+09:00"),
                                      capture_output=True, text=True, encoding="utf-8") for _ in range(2)]
            self.assertTrue(all(p.returncode == 0 and p.stderr == "" for p in outputs))
            self.assertEqual([len(json.loads(p.stdout)["send"]) for p in outputs], [1, 0])
            self.assertEqual(read_json(root / "doses.json")[KEY]["sentStep"], 2)

    def test_simultaneous_due_processes_claim_one_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write_json(root / "schedule.json", schedule())
            processes = [subprocess.Popen(self.command(root, "due", "--now", "2026-11-01T17:12:00+09:00"),
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, encoding="utf-8") for _ in range(2)]
            outputs = [p.communicate(timeout=10) for p in processes]
            self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
            self.assertEqual(sum(len(json.loads(out)["send"]) for out, _ in outputs), 1)

    def test_simultaneous_mark_and_skip_preserve_taken_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write_json(root / "schedule.json", schedule())
            commands = [self.command(root, "mark", KEY, "--source", "chat", "--at", "2026-11-01T17:05:00+09:00"),
                        self.command(root, "skip", KEY, "--at", "2026-11-01T17:05:00+09:00")]
            processes = [subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, encoding="utf-8") for c in commands]
            outputs = [p.communicate(timeout=10) for p in processes]
            self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
            self.assertEqual(read_json(root / "doses.json")[KEY]["state"], "TAKEN")

    def test_invalid_now_does_not_touch_existing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write_json(root / "schedule.json", schedule())
            atomic_write_json(root / "doses.json", {"previous": True})
            p = subprocess.run(self.command(root, "due", "--now", "2026-11-01T17:12:00"),
                               capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(json.loads(p.stdout)["error"]["code"], "INVALID_INPUT")
            self.assertEqual(read_json(root / "doses.json"), {"previous": True})


if __name__ == "__main__":
    unittest.main()
