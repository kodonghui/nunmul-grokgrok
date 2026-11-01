from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.common import ToolError, atomic_write_json, parse_instant, read_json
from tools.dose_rules import VOICE_PATH
from tools.make_schedule import build_schedule
from tools.med_store import (acknowledge_calendar, add_medicines, build_store_schedule,
                             calendar_plan, clear_schedule, delete_medicine, empty_store,
                             list_medicines, refresh_calendar, set_schedule, stop_medicine,
                             store_due, store_mark, transact)


ROOT = Path(__file__).resolve().parents[1]
VOICE = read_json(VOICE_PATH)
KEY = "2026-11-01|17:00"


def at(value="13:00", day="2026-11-01"):
    return parse_instant(f"{day}T{value}:00+09:00")


def payload(code="200001", name="가상약A정", **changes):
    item = {"name": name, "itemSeq": code, "dosePerTime": 1, "timing": "식후 30분",
            "startDate": "2026-11-01", "durationDays": 5, "doseTimes": ["17:00"],
            "confirmedAt": "2026-11-01T13:00:00+09:00"}
    item.update(changes)
    return {"items": [item]}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "medication_store.json"

    def run_action(self, action, when=None, revision=None, changes=True):
        return transact(self.path, action, when or at(), revision, include_changes=changes)

    def add(self, value=None, when=None, request_id=None):
        value = payload() if value is None else value
        return self.run_action(lambda s: add_medicines(s, value, when or at(), request_id), when)

    def state(self):
        return read_json(self.path)

    def ids(self):
        return list(self.state()["medicines"])

    def two(self):
        a = self.add()["added"][0]
        b = self.add(payload("200002", "가상약B정"))["added"][0]
        return a, b

    def mark(self, key=KEY, ids=None, when=None):
        return self.run_action(lambda s: store_mark(s, when or at("17:05"), "chat", key, ids, voice=VOICE), when or at("17:05"))

    def acknowledge_all(self, when=None):
        for job in self.run_action(calendar_plan, when)["changes"]:
            self.run_action(lambda s, j=job: acknowledge_calendar(s, j["key"], j["fingerprint"], "event-" + j["key"]), when)


class StoreTests(StoreCase):
    def test_appending_preserves_existing_medicine(self):
        first = self.add()["added"][0]
        before = deepcopy(self.state()["medicines"][first])
        self.add(payload("200002", "가상약B정"))
        self.assertEqual(len(self.state()["medicines"]), 2)
        self.assertEqual(self.state()["medicines"][first], before)
        view = build_store_schedule(self.state())
        self.assertEqual(len(view["occurrences"]), 5)
        self.assertEqual(len(view["occurrences"][0]["doses"]), 2)

    def test_start_duration_and_confirmation_belong_to_each_medicine(self):
        a = self.add(payload(confirmedAt="2026-11-01T06:00:00+09:00", doseTimes=["07:00"]))["added"][0]
        b = self.add(payload("200002", "가상약B정", startDate="2026-11-03", durationDays=2))["added"][0]
        state = self.state()
        self.assertEqual(state["medicines"][a]["schedule"]["endDate"], "2026-11-05")
        self.assertEqual(state["medicines"][b]["schedule"]["endDate"], "2026-11-04")
        first_a = next(d for d in state["doses"].values() if d["medicineId"] == a)
        self.assertEqual(first_a["state"], "PENDING")

    def test_same_request_is_idempotent_with_and_without_explicit_id(self):
        for request in (None, "registration-1"):
            path = self.root / ("auto.json" if request is None else "explicit.json")
            action = lambda s: add_medicines(s, payload(), at(), request)
            first = transact(path, action, at())
            second = transact(path, action, at("14:00"))
            self.assertEqual(first["added"], second["added"])
            self.assertEqual(first["revision"], second["revision"])
            self.assertTrue(second["replayed"])

    def test_reused_request_id_with_different_content_rolls_back(self):
        self.add(request_id="registration-1")
        before = self.path.read_bytes()
        with self.assertRaises(ToolError) as caught:
            self.add(payload("200002", "가상약B정"), request_id="registration-1")
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_existing_item_rejects_the_entire_batch(self):
        self.add()
        before = self.path.read_bytes()
        value = payload("200002", "가상약B정")
        value["items"].extend(payload()["items"])
        with self.assertRaises(ToolError) as caught:
            self.add(value)
        self.assertEqual(caught.exception.code, "DUPLICATE_MEDICINE")
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_batch_and_invalid_second_item_save_nothing(self):
        for second in (payload()["items"][0], payload("200002", "가상약B정", dosePerTime=0.25)["items"][0]):
            value = payload()
            value["items"].append(second)
            with self.assertRaises(ToolError):
                self.add(value)
            self.assertFalse(self.path.exists())

    def test_manual_medicine_has_no_invented_code_or_dose(self):
        value = {"items": [{"name": "가상 직접 입력 약", "itemSeq": None}]}
        medicine_id = self.add(value)["added"][0]
        med = self.state()["medicines"][medicine_id]
        self.assertIsNone(med["itemSeq"])
        self.assertIsNone(med["dosePerTime"])
        self.assertIsNone(med["schedule"])
        self.assertEqual(self.run_action(lambda s: list_medicines(s, at()))["medicines"][0]["status"], "UNSCHEDULED")
        with self.assertRaises(ToolError):
            self.add({"items": [{"name": "가상직접입력약", "itemSeq": None}]})

    def test_unknown_input_fields_do_not_enter_the_store(self):
        value = payload()
        value["patientName"] = "합성 개인정보 필드"
        value["items"][0]["hospital"] = "합성 병원 필드"
        self.add(value)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("patientName", text)
        self.assertNotIn("hospital", text)
        self.assertNotIn("합성 개인정보", text)

    def test_schedule_change_keeps_other_medicine_and_completed_record(self):
        a, b = self.two()
        self.mark(ids=[a])
        previous_b = deepcopy(self.state()["medicines"][b])
        old_taken = next((k, deepcopy(d)) for k, d in self.state()["doses"].items() if d["medicineId"] == a and d["state"] == "TAKEN")
        self.run_action(lambda s: set_schedule(s, a, {"doseTimes": ["18:00"]}, at("17:10")), at("17:10"))
        state = self.state()
        self.assertEqual(state["medicines"][b], previous_b)
        self.assertEqual(state["medicines"][a]["schedule"]["version"], 2)
        self.assertEqual(state["doses"][old_taken[0]], old_taken[1])
        self.assertTrue(any(d["medicineId"] == a and d["active"] and "T18:00:" in d["at"] for d in state["doses"].values()))
        self.assertTrue(any(d["medicineId"] == a and not d["active"] and d["skipReason"] == "SCHEDULE_CHANGED" for d in state["doses"].values()))

    def test_repeat_identical_schedule_does_not_create_a_new_version(self):
        medicine_id = self.add()["added"][0]
        old_revision = self.state()["revision"]
        result = self.run_action(lambda s: set_schedule(s, medicine_id, {"doseTimes": ["17:00"]}, at("14:00")), at("14:00"))
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["revision"], old_revision)

    def test_setting_schedule_on_unscheduled_medicine_needs_confirmed_dose(self):
        medicine_id = self.add({"items": [{"name": "가상약", "itemSeq": None}]})["added"][0]
        plan = {"startDate": "2026-11-01", "durationDays": 2, "doseTimes": ["17:00"]}
        with self.assertRaises(ToolError):
            self.run_action(lambda s: set_schedule(s, medicine_id, plan, at()))
        plan["dosePerTime"] = 0.5
        self.run_action(lambda s: set_schedule(s, medicine_id, plan, at()))
        self.assertEqual(self.state()["medicines"][medicine_id]["dosePerTime"], 0.5)

    def test_stop_only_selected_medicine_immediately_and_keep_taken(self):
        a, b = self.two()
        self.mark(ids=[a])
        self.run_action(lambda s: stop_medicine(s, a, at("17:10")), at("17:10"))
        state = self.state()
        self.assertEqual(state["medicines"][a]["schedule"]["endDate"], "2026-11-01")
        self.assertEqual(state["medicines"][a]["schedule"]["stoppedAt"], "2026-11-01T17:10:00+09:00")
        self.assertTrue(any(d["medicineId"] == a and d["state"] == "TAKEN" for d in state["doses"].values()))
        pending_b = [d for d in state["doses"].values() if d["medicineId"] == b and d["state"] == "PENDING"]
        self.assertEqual(len(pending_b), 5)
        self.assertFalse(any(d["medicineId"] == a and d["state"] == "PENDING" for d in state["doses"].values()))

    def test_stop_is_idempotent_and_future_or_finished_stop_is_rejected(self):
        medicine_id = self.add()["added"][0]
        first = self.run_action(lambda s: stop_medicine(s, medicine_id, at("14:00")), at("14:00"))
        second = self.run_action(lambda s: stop_medicine(s, medicine_id, at("15:00")), at("15:00"))
        self.assertEqual(first["revision"], second["revision"])
        other = self.add(payload("200002", "가상약B정", startDate="2026-11-03"))["added"][0]
        for when in (at(), at("13:00", "2026-11-10")):
            with self.assertRaises(ToolError) as caught:
                self.run_action(lambda s: stop_medicine(s, other, when), when)
            self.assertEqual(caught.exception.code, "SCHEDULE_CONFLICT")

    def test_clear_schedule_keeps_medicine_and_is_idempotent(self):
        medicine_id = self.add()["added"][0]
        first = self.run_action(lambda s: clear_schedule(s, medicine_id, at()))
        second = self.run_action(lambda s: clear_schedule(s, medicine_id, at("14:00")), at("14:00"))
        self.assertEqual(first["revision"], second["revision"])
        med = self.state()["medicines"][medicine_id]
        self.assertIsNone(med["schedule"])
        self.assertEqual(med["name"], "가상약A정")
        self.assertEqual(med["dosePerTime"], 1)

    def test_delete_hides_only_selected_medicine_and_keeps_history(self):
        a, b = self.two()
        self.mark(ids=[a])
        first = self.run_action(lambda s: delete_medicine(s, a, at("17:10")), at("17:10"))
        second = self.run_action(lambda s: delete_medicine(s, a, at("17:20")), at("17:20"))
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual([m["id"] for m in list_medicines(self.state(), at())["medicines"]], [b])
        self.assertEqual(len(list_medicines(self.state(), at(), True)["medicines"]), 2)
        self.assertTrue(any(d["medicineId"] == a and d["state"] == "TAKEN" for d in self.state()["doses"].values()))

    def test_deleted_medicine_can_be_registered_with_new_identity_without_inheriting_taken(self):
        original = payload(confirmedAt="2026-11-01T16:00:00+09:00")
        old_id = self.add(original, at("16:00"), "old-request")["added"][0]
        self.mark(ids=[old_id], when=at("16:50"))
        self.run_action(lambda s: delete_medicine(s, old_id, at("16:52")), at("16:52"))
        fresh = payload(confirmedAt="2026-11-01T16:55:00+09:00")
        new_id = self.add(fresh, at("16:55"), "new-request")["added"][0]
        self.assertNotEqual(old_id, new_id)
        new_records = [d for d in self.state()["doses"].values() if d["medicineId"] == new_id]
        self.assertTrue(all(d["state"] == "PENDING" for d in new_records))
        result = self.run_action(lambda s: store_due(s, at("17:05"), VOICE), at("17:05"))
        self.assertEqual(result["send"][0]["medicineIds"], [new_id])

    def test_rescheduling_does_not_remind_again_for_an_already_taken_same_time(self):
        medicine_id = self.add()["added"][0]
        self.mark(ids=[medicine_id], when=at("16:50"))
        self.run_action(lambda s: set_schedule(s, medicine_id, {"timing": "확인된 표시 문구"}, at("16:55")), at("16:55"))
        active = [d for d in self.state()["doses"].values() if d["active"] and d["at"] == "2026-11-01T17:00:00+09:00"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["state"], "TAKEN")
        self.assertEqual(self.run_action(lambda s: store_due(s, at("17:05"), VOICE), at("17:05"))["send"], [])

    def test_new_schedule_after_stop_does_not_reactivate_old_cancelled_dose_ids(self):
        medicine_id = self.add()["added"][0]
        self.run_action(lambda s: stop_medicine(s, medicine_id, at("14:00")), at("14:00"))
        old_records = deepcopy(self.state()["doses"])
        self.run_action(lambda s: set_schedule(s, medicine_id, {"durationDays": 5}, at("15:00")), at("15:00"))
        state = self.state()
        for key, old in old_records.items():
            self.assertEqual(state["doses"][key], old)
        active = [d for d in state["doses"].values() if d["active"]]
        self.assertTrue(active)
        self.assertTrue(all(d["scheduleVersion"] == 2 and d["state"] == "PENDING" for d in active))

    def test_calendar_delete_ack_is_idempotent(self):
        medicine_id = self.add()["added"][0]
        self.acknowledge_all()
        self.run_action(lambda s: delete_medicine(s, medicine_id, at()))
        job = self.state()["calendar"]["pending"][KEY]
        self.assertEqual(job["operation"], "DELETE")
        self.run_action(lambda s: acknowledge_calendar(s, KEY, job["fingerprint"]))
        repeat = self.run_action(lambda s: acknowledge_calendar(s, KEY, job["fingerprint"]))
        self.assertTrue(repeat["replayed"])
        self.assertNotIn(KEY, self.state()["calendar"]["applied"])

    def test_mark_without_key_selects_nearest_pending_for_selected_medicine(self):
        a = self.add()["added"][0]
        b = self.add(payload("200002", "가상약B정", doseTimes=["17:30"]))["added"][0]
        result = self.run_action(lambda s: store_mark(s, at("17:25"), "chat", medicine_ids=[a], voice=VOICE), at("17:25"))
        self.assertEqual(result["marked"], KEY)
        self.assertEqual(result["medicineIds"], [a])
        self.assertTrue(all(d["state"] == "PENDING" for d in self.state()["doses"].values() if d["medicineId"] == b))

    def test_partial_mark_and_repeated_mark_keep_first_confirmation(self):
        a, b = self.two()
        self.mark(ids=[a])
        before = {k: deepcopy(d) for k, d in self.state()["doses"].items() if d["state"] == "TAKEN"}
        self.mark(ids=[a], when=at("17:10"))
        for key, record in before.items():
            self.assertEqual(self.state()["doses"][key], record)
        view = build_store_schedule(self.state())
        self.assertIn("일부 확인", next(e for e in view["calendarEvents"] if e["key"] == KEY)["summary"])
        self.mark(ids=[b], when=at("17:10"))
        self.assertTrue(next(e for e in build_store_schedule(self.state())["calendarEvents"] if e["key"] == KEY)["summary"].startswith("✅"))

    def test_skipping_one_medicine_leaves_the_other_for_reminder(self):
        a, b = self.two()
        self.run_action(lambda s: store_mark(s, at("17:05"), key=KEY, medicine_ids=[a], skip=True), at("17:05"))
        result = self.run_action(lambda s: store_due(s, at("17:12"), VOICE), at("17:12"))
        self.assertEqual(result["send"][0]["medicineIds"], [b])
        self.assertEqual(result["send"][0]["step"], 2)

    def test_grouped_due_is_once_per_phase_and_missed_once(self):
        self.two()
        for when, step in (("17:05", 1), ("17:12", 2), ("17:31", 3), ("18:31", 4)):
            result = self.run_action(lambda s, t=when: store_due(s, at(t), VOICE), at(when))
            self.assertEqual([m["step"] for m in result["send"]], [step])
        self.assertEqual(self.run_action(lambda s: store_due(s, at("18:31"), VOICE), at("18:31"))["send"], [])

    def test_new_medicine_is_skipped_before_its_confirmation_without_changing_old_record(self):
        a = self.add()["added"][0]
        self.mark(ids=[a])
        b = self.add(payload("200002", "가상약B정", confirmedAt="2026-11-01T17:10:00+09:00"), at("17:10"))["added"][0]
        first_b = next(d for d in self.state()["doses"].values() if d["medicineId"] == b)
        self.assertEqual(first_b["state"], "SKIPPED")
        old_a = next(d for d in self.state()["doses"].values() if d["medicineId"] == a)
        self.assertEqual(old_a["state"], "TAKEN")

    def test_quiet_hours_update_state_without_later_replay(self):
        self.add(payload(doseTimes=["22:00"]))
        first = self.run_action(lambda s: store_due(s, at("23:31"), VOICE), at("23:31"))
        self.assertEqual(first["send"], [])
        self.assertEqual(next(iter(self.state()["doses"].values()))["state"], "MISSED")
        self.assertEqual(self.run_action(lambda s: store_due(s, at("07:00", "2026-11-02"), VOICE), at("07:00", "2026-11-02"))["send"], [])

    def test_revisit_dates_are_per_medicine_and_same_day_is_grouped(self):
        self.two()
        self.add(payload("200003", "가상약C정", durationDays=20))
        view = build_store_schedule(self.state())
        self.assertEqual(view["revisitDates"], ["2026-11-02", "2026-11-17"])
        revisits = [e for e in view["calendarEvents"] if e["key"].startswith("revisit|")]
        self.assertEqual(len(revisits), 2)
        result = self.run_action(lambda s: store_due(s, at("09:00", "2026-11-02"), VOICE), at("09:00", "2026-11-02"))
        only_visit = [m for m in result["send"] if m["key"].startswith("revisit|")]
        self.assertEqual(len(only_visit), 1)
        self.assertEqual(len(only_visit[0]["medicineIds"]), 2)

    def test_calendar_failure_keeps_same_pending_work_and_ack_is_idempotent(self):
        self.add()
        first = self.run_action(calendar_plan, changes=False)["changes"]
        second = self.run_action(calendar_plan, at("14:00"), changes=False)["changes"]
        self.assertEqual(first, second)
        job = first[0]
        self.run_action(lambda s: acknowledge_calendar(s, job["key"], job["fingerprint"], "fixture-event"))
        repeat = self.run_action(lambda s: acknowledge_calendar(s, job["key"], job["fingerprint"], "fixture-event"))
        self.assertTrue(repeat["replayed"])
        self.assertNotIn(job["key"], self.state()["calendar"]["pending"])

    def test_stop_updates_group_with_remaining_medicine_instead_of_deleting_it(self):
        a, _ = self.two()
        self.acknowledge_all()
        self.run_action(lambda s: stop_medicine(s, a, at("14:00")), at("14:00"))
        job = self.state()["calendar"]["pending"]["2026-11-02|17:00"]
        self.assertEqual(job["operation"], "UPSERT")
        self.assertIn("가상약B정", job["event"]["description"])
        self.assertNotIn("가상약A정", job["event"]["description"])

    def test_last_medicine_deletes_only_applied_future_events(self):
        medicine_id = self.add()["added"][0]
        self.acknowledge_all()
        self.run_action(lambda s: delete_medicine(s, medicine_id, at("18:00")), at("18:00"))
        jobs = self.state()["calendar"]["pending"]
        self.assertNotIn(KEY, jobs)
        self.assertEqual(jobs["2026-11-02|17:00"]["operation"], "DELETE")
        self.assertIsNotNone(jobs["2026-11-02|17:00"]["eventId"])

    def test_pending_delete_survives_time_advance_until_ack(self):
        medicine_id = self.add()["added"][0]
        self.acknowledge_all()
        self.run_action(lambda s: delete_medicine(s, medicine_id, at("16:00")), at("16:00"))
        job = deepcopy(self.state()["calendar"]["pending"][KEY])
        self.run_action(calendar_plan, at("18:00"))
        self.assertEqual(self.state()["calendar"]["pending"][KEY], job)

    def test_cancel_unacknowledged_create_keeps_delete_by_key_until_absence_is_confirmed(self):
        medicine_id = self.add()["added"][0]
        self.run_action(lambda s: delete_medicine(s, medicine_id, at()))
        job = deepcopy(self.state()["calendar"]["pending"][KEY])
        self.assertEqual(job["operation"], "DELETE")
        self.assertIsNone(job["eventId"])
        self.run_action(calendar_plan, at("18:00"))
        self.assertEqual(self.state()["calendar"]["pending"][KEY], job)
        self.run_action(lambda s: acknowledge_calendar(s, KEY, job["fingerprint"]), at("18:00"))
        self.assertNotIn(KEY, self.state()["calendar"]["pending"])

    def test_stale_calendar_ack_is_rejected_after_membership_changes(self):
        self.add()
        job = self.state()["calendar"]["pending"][KEY]
        self.add(payload("200002", "가상약B정"))
        before = self.path.read_bytes()
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: acknowledge_calendar(s, KEY, job["fingerprint"], "fixture-event"))
        self.assertEqual(caught.exception.code, "STALE_CALENDAR_CHANGE")
        self.assertEqual(self.path.read_bytes(), before)

    def test_revision_conflict_and_failed_save_preserve_store(self):
        self.add()
        before = self.path.read_bytes()
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: add_medicines(s, payload("200002", "가상약B정"), at()), revision=0)
        self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
        with patch("tools.common.os.replace", side_effect=OSError), self.assertRaises(ToolError):
            self.add(payload("200002", "가상약B정"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_schedule_and_missing_target_are_rejected_without_changes(self):
        medicine_id = self.add()["added"][0]
        before = self.path.read_bytes()
        for change in ({"doseTimes": ["17:00", "17:00"]}, {"patientName": "합성"},
                       {"durationDays": 0}, {"dosePerTime": 0.25}, {"endDate": "2026-10-31"}):
            with self.assertRaises(ToolError):
                self.run_action(lambda s, p=change: set_schedule(s, medicine_id, p, at()))
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ToolError):
            self.run_action(lambda s: stop_medicine(s, "unknown", at()))


class MigrationTests(StoreCase):
    def legacy(self, slots=None):
        source = {"startDate": "2026-11-01", "confirmedAt": "2026-11-01T13:00:00+09:00",
                  "items": [{"readNo": 1, "itemSeq": "100001", "name": "가상 기존약정", "dosePerTime": 1,
                             "dailyCount": 2, "dosePeriods": None, "durationDays": 5, "timing": "식후 30분"}]}
        schedule = build_schedule(source, slots, today=date(2026, 11, 1))
        doses = {"2026-11-01|17:00": {"state": "TAKEN", "sentStep": 2, "takenAt": "2026-11-01T17:12:00+09:00", "source": "chat"},
                 "revisit|2026-11-02": {"sentStep": 1}}
        for name, value in (("prescription.json", source), ("schedule.json", schedule), ("doses.json", doses)):
            atomic_write_json(self.root / name, value)
        if slots:
            atomic_write_json(self.root / "slots.json", slots)
        return source

    def test_legacy_migration_preserves_original_files_and_records(self):
        self.legacy()
        original = {p.name: p.read_bytes() for p in self.root.glob("*.json")}
        result = self.run_action(lambda s: list_medicines(s, at("18:00")), at("18:00"))
        self.assertEqual(len(result["medicines"]), 1)
        self.assertTrue(any(d["state"] == "TAKEN" and d["source"] == "chat" for d in self.state()["doses"].values()))
        self.assertEqual(self.state()["reminders"][KEY]["sentStep"], 2)
        self.assertEqual(self.state()["reminders"]["revisit|2026-11-02"]["sentStep"], 1)
        for name, raw in original.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        self.add(payload())
        self.assertEqual(len(self.state()["medicines"]), 2)

    def test_legacy_custom_slots_can_be_migrated(self):
        self.legacy({"MORNING": "08:30"})
        self.run_action(lambda s: list_medicines(s, at()))
        self.assertIn("08:30", next(iter(self.state()["medicines"].values()))["schedule"]["doseTimes"])

    def test_legacy_source_mismatch_is_not_guessed(self):
        self.legacy()
        old = read_json(self.root / "schedule.json")
        old["confirmedAt"] = "2026-11-01T12:00:00+09:00"
        atomic_write_json(self.root / "schedule.json", old)
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: list_medicines(s, at()))
        self.assertEqual(caught.exception.code, "MIGRATION_CONFLICT")
        self.assertFalse(self.path.exists())

    def test_legacy_unknown_dose_or_missing_schedule_is_rejected(self):
        self.legacy()
        old = read_json(self.root / "doses.json")
        old["2026-10-01|17:00"] = deepcopy(old[KEY])
        atomic_write_json(self.root / "doses.json", old)
        with self.assertRaises(ToolError):
            self.run_action(lambda s: list_medicines(s, at()))
        self.assertFalse(self.path.exists())
        (self.root / "schedule.json").unlink()
        with self.assertRaises(ToolError):
            self.run_action(lambda s: list_medicines(s, at()))

    def test_orphan_legacy_schedule_is_not_silently_replaced_by_empty_list(self):
        atomic_write_json(self.root / "schedule.json", {"occurrences": []})
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: list_medicines(s, at()))
        self.assertEqual(caught.exception.code, "MIGRATION_CONFLICT")
        self.assertFalse(self.path.exists())


class StoreCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = self.root / "state/medication_store.json"
        self.environ = os.environ.copy()
        self.environ.update(PUBLIC_DATA_SERVICE_KEY="", PUBLIC_DATA_BASE_URL="https://apis.data.go.kr")

    def command(self, script, *args):
        return [sys.executable, "-S", "-B", str(ROOT / "tools" / script), *map(str, args)]

    def call(self, script, *args, success=True):
        p = subprocess.run(self.command(script, *args), cwd=self.root, env=self.environ,
                           capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(p.returncode == 0, success, p.stdout + p.stderr)
        self.assertEqual(p.stderr, "")
        self.assertEqual(len(p.stdout.splitlines()), 1)
        return json.loads(p.stdout)

    def input(self, filename, value):
        path = self.root / filename
        atomic_write_json(path, value)
        return path

    def add(self, value=None):
        source = self.input("input.json", value or payload())
        return self.call("my_meds.py", "add", "--in", source, "--at", "2026-11-01T13:00:00+09:00")

    def test_schedule_export_cannot_overwrite_medicine_store(self):
        self.add()
        before = self.store.read_bytes()
        result = self.call("make_schedule.py", "--store", self.store, "--out", self.store, success=False)
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.store.read_bytes(), before)

    def test_corrupt_dose_record_returns_json_error_and_preserves_original(self):
        self.add()
        state = read_json(self.store)
        next(iter(state["doses"].values())).pop("name")
        atomic_write_json(self.store, state)
        before = self.store.read_bytes()
        result = self.call("my_meds.py", "list", success=False)
        self.assertEqual(result["error"]["code"], "INVALID_STORE")
        self.assertEqual(self.store.read_bytes(), before)

    def test_cli_add_list_view_due_partial_mark_stop_and_delete(self):
        medicine_id = self.add()["added"][0]
        listed = self.call("my_meds.py", "list")
        self.assertEqual(listed["medicines"][0]["id"], medicine_id)
        target = self.root / "view.json"
        view = self.call("make_schedule.py", "--store", self.store, "--out", target)
        self.assertEqual(len(view["occurrences"]), 5)
        due = self.call("dose_state.py", "due", "--now", "2026-11-01T17:12:00+09:00")
        self.assertEqual(due["send"][0]["step"], 2)
        marked = self.call("dose_state.py", "mark", KEY, "--medicine-id", medicine_id, "--source", "chat", "--at", "2026-11-01T17:13:00+09:00")
        self.assertEqual(marked["state"], "TAKEN")
        self.call("my_meds.py", "stop", medicine_id, "--at", "2026-11-01T17:20:00+09:00")
        self.call("my_meds.py", "delete", medicine_id, "--at", "2026-11-01T17:21:00+09:00")
        self.assertEqual(self.call("my_meds.py", "list")["medicines"], [])

    def test_cli_modes_cannot_be_mixed(self):
        result = self.call("dose_state.py", "due", "--now", "2026-11-01T17:12:00+09:00", "--store", self.store,
                           "--doses", self.root / "doses.json", success=False)
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")

    def test_simultaneous_adds_do_not_overwrite_medicines(self):
        paths = [self.input("a.json", payload()), self.input("b.json", payload("200002", "가상약B정"))]
        processes = [subprocess.Popen(self.command("my_meds.py", "add", "--in", p, "--at", "2026-11-01T13:00:00+09:00"),
                                      cwd=self.root, env=self.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8") for p in paths]
        outputs = [p.communicate(timeout=15) for p in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
        self.assertEqual(len(read_json(self.store)["medicines"]), 2)

    def test_simultaneous_due_returns_one_message(self):
        self.add()
        command = self.command("dose_state.py", "due", "--now", "2026-11-01T17:12:00+09:00")
        processes = [subprocess.Popen(command, cwd=self.root, env=self.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8") for _ in range(2)]
        outputs = [p.communicate(timeout=15) for p in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
        self.assertEqual(sum(len(json.loads(out)["send"]) for out, _ in outputs), 1)

    def test_simultaneous_identical_add_is_registered_once(self):
        source = self.input("input.json", payload())
        command = self.command("my_meds.py", "add", "--in", source, "--at", "2026-11-01T13:00:00+09:00")
        processes = [subprocess.Popen(command, cwd=self.root, env=self.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8") for _ in range(2)]
        outputs = [p.communicate(timeout=15) for p in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
        self.assertEqual(json.loads(outputs[0][0])["added"], json.loads(outputs[1][0])["added"])
        self.assertEqual(len(read_json(self.store)["medicines"]), 1)

    def test_simultaneous_mark_and_skip_do_not_erase_taken(self):
        medicine_id = self.add()["added"][0]
        commands = [self.command("dose_state.py", "mark", KEY, "--medicine-id", medicine_id,
                                 "--source", "chat", "--at", "2026-11-01T17:05:00+09:00"),
                    self.command("dose_state.py", "skip", KEY, "--medicine-id", medicine_id,
                                 "--at", "2026-11-01T17:05:00+09:00")]
        processes = [subprocess.Popen(command, cwd=self.root, env=self.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8") for command in commands]
        outputs = [p.communicate(timeout=15) for p in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), outputs)
        first = next(d for d in read_json(self.store)["doses"].values() if d["at"] == "2026-11-01T17:00:00+09:00")
        self.assertEqual(first["state"], "TAKEN")

    def test_missing_store_fields_return_json_error(self):
        self.add()
        state = read_json(self.store)
        next(iter(state["medicines"].values())).pop("schedule")
        atomic_write_json(self.store, state)
        result = self.call("my_meds.py", "list", success=False)
        self.assertEqual(result["error"]["code"], "INVALID_STORE")

    def test_invalid_input_and_corrupt_store_are_json_errors(self):
        self.add()
        state = read_json(self.store)
        next(iter(state["doses"].values()))["state"] = "CORRUPT"
        atomic_write_json(self.store, state)
        result = self.call("my_meds.py", "list", success=False)
        self.assertEqual(result["error"]["code"], "INVALID_STORE")
        result = self.call("my_meds.py", "set-schedule", "unknown", "--in", self.root / "missing.json", success=False)
        self.assertEqual(result["error"]["code"], "READ_FAILED")


if __name__ == "__main__":
    unittest.main()
