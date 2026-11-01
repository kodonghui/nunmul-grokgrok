from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import family_store as family, med_store
from tools.common import ToolError, atomic_write_json, parse_instant, read_json
from tools.desk_event import accept, main, read_event, validate_event
from tools.dose_rules import VOICE_PATH


ROOT = Path(__file__).resolve().parents[1]
AT = parse_instant("2026-11-01T13:00:00+09:00")
NOW = parse_instant("2026-11-01T17:05:00+09:00")


def identify(packet):
    value = {key: packet[key] for key in ("profileId", "key", "doseIds")}
    packet["requestId"] = "desk_" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return packet


class DeskEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "state/medication_store.json"
        self.mom = self.action(lambda s: family.create_profile(s, "엄마", AT))["profileId"]
        self.grandma = self.action(lambda s: family.create_profile(s, "할머니", AT))["profileId"]
        self.first = self.add(self.mom, "900001", "가상A정")
        self.second = self.add(self.mom, "900002", "가상B정")
        self.add(self.grandma, "900001", "가상A정")

    def action(self, function, now=AT, **options):
        return family.transact(self.path, function, now, **options)

    def add(self, profile, code, name):
        value = {"items": [{"name": name, "itemSeq": code, "dosePerTime": 1, "startDate": "2026-11-01",
                           "durationDays": 5, "doseTimes": ["17:00"]}]}
        return self.action(lambda s: family.medicine_action(s, profile, "add", AT, value))["added"][0]

    def state(self):
        return read_json(self.path)

    def packet(self, profile=None, medicines=None):
        profile = profile or self.mom
        state = self.state()
        view = family.schedule(state, profile)
        occurrence = next(row for row in view["occurrences"] if row["at"] == "2026-11-01T17:00:00+09:00")
        selected = sorted((d for d in occurrence["doses"] if medicines is None or d["medicineId"] in medicines), key=lambda d: d["doseId"])
        return identify({"event": "dose_taken", "source": "desk", "pressedAt": "2026-11-01T17:05:00+09:00",
                         "profileId": profile, "key": occurrence["key"], "doseIds": [d["doseId"] for d in selected],
                         "medicineIds": [d["medicineId"] for d in selected], "scheduleRevision": state["revision"]})

    def receive(self, packet=None, bound=None, now=NOW):
        packet = self.packet() if packet is None else packet
        return self.action(lambda s: accept(s, packet, bound or self.mom, now), now, require_existing=True)

    def reject(self, packet, code, bound=None, now=NOW):
        before = self.path.read_bytes()
        with self.assertRaises(ToolError) as caught:
            self.receive(packet, bound, now)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.path.read_bytes(), before)

    def test_exact_family_and_subset_recorded_without_touching_other_doses(self):
        other = deepcopy(self.state()["profiles"][self.grandma])
        event = self.packet(medicines=[self.first])
        result = self.receive(event)
        self.assertTrue(result["recorded"])
        self.assertTrue(result["notify"])
        self.assertEqual(len(result["changedDoseIds"]), 1)
        self.assertEqual(self.state()["profiles"][self.grandma], other)
        store = self.state()["profiles"][self.mom]["store"]
        record = store["doses"][event["doseIds"][0]]
        self.assertEqual((record["state"], record["source"], record["takenAt"]), ("TAKEN", "desk", event["pressedAt"]))
        self.assertEqual(record["recordedBy"], "account_owner")
        self.assertTrue(any(d["state"] == "PENDING" and d["medicineId"] == self.second for d in store["doses"].values()))
        change = next(job for job in result["calendarChanges"] if job["key"] == event["key"])
        self.assertIn("일부 확인", change["event"]["summary"])

    def test_replay_is_no_write_and_no_second_notification(self):
        event = self.packet()
        first = self.receive(event)
        before = self.path.read_bytes()
        second = self.receive(event)
        self.assertTrue(second["replayed"])
        self.assertTrue(second["recorded"])
        self.assertFalse(second["notify"])
        self.assertIsNone(second["say"])
        self.assertEqual(first["doseIds"], second["doseIds"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_accepted_receipt_replays_after_deletion_and_expiry_without_new_mark(self):
        event = self.packet()
        first = self.receive(event)
        self.action(lambda s: family.medicine_action(s, self.mom, "delete", NOW, medicine_id=self.first), NOW)
        before = self.path.read_bytes()
        result = self.receive(event, now=NOW + timedelta(hours=5))
        self.assertTrue(result["replayed"])
        self.assertEqual(result["doseIds"], first["doseIds"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_request_id_cannot_change_press_time_or_medicine_list(self):
        event = self.packet(medicines=[self.first])
        self.receive(event)
        for changed in ({**event, "pressedAt": "2026-11-01T17:06:00+09:00"}, {**event, "medicineIds": [self.second]}):
            self.reject(changed, "REQUEST_CONFLICT")

    def test_bound_profile_and_scoped_key_never_guess_another_family(self):
        event = self.packet()
        self.reject(event, "PROFILE_MISMATCH", bound=self.grandma)
        self.reject({**event, "key": event["key"].replace(self.mom, self.grandma)}, "PROFILE_MISMATCH")
        self.reject(event, "INVALID_DESK_EVENT", bound="엄마")

    def test_cross_profile_dose_and_different_occurrence_are_rejected(self):
        event = self.packet(medicines=[self.first])
        other = self.packet(self.grandma)
        self.reject(identify({**event, "doseIds": other["doseIds"], "medicineIds": other["medicineIds"]}), "DESK_TARGET_MISMATCH")
        wrong = event["doseIds"][0].replace("2026-11-01", "2026-11-02")
        self.reject(identify({**event, "doseIds": [wrong]}), "DESK_TARGET_MISMATCH")

    def test_malformed_fields_and_request_digest_are_rejected_atomically(self):
        event = self.packet()
        for invalid in ({**event, "patientName": "private-marker"}, {**event, "source": "chat"}, {**event, "event": "arbitrary.shell"},
                        {**event, "requestId": "desk_" + "0" * 64}, {**event, "scheduleRevision": True},
                        {**event, "doseIds": []}, {**event, "doseIds": event["doseIds"] * 2},
                        {**event, "medicineIds": [True]}, {**event, "medicineIds": []}, {**event, "doseIds": [None]},
                        {**event, "pressedAt": "2026-11-01T17:05:00"},
                        identify({**event, "doseIds": [chr(0xD800)], "medicineIds": [self.first]})):
            with self.subTest(invalid=invalid):
                before = self.path.read_bytes()
                with self.assertRaises(ToolError):
                    self.receive(invalid)
                self.assertEqual(self.path.read_bytes(), before)

    def test_medicine_mapping_and_future_snapshot_revision_are_rejected(self):
        event = self.packet(medicines=[self.first])
        self.reject({**event, "medicineIds": [self.second]}, "DESK_TARGET_MISMATCH")
        self.reject({**event, "scheduleRevision": self.state()["revision"] + 1}, "DESK_STALE_SCHEDULE")

    def test_unrelated_revision_changes_do_not_reject_unchanged_doses(self):
        event = self.packet()
        self.action(lambda s: family.update_profile(s, self.grandma, {"alias": "할머니 시연"}, AT))
        self.assertLess(event["scheduleRevision"], self.state()["revision"])
        self.assertTrue(self.receive(event)["recorded"])

    def test_stop_clear_delete_and_schedule_change_invalidate_old_snapshot(self):
        baseline = self.state()
        event = self.packet(medicines=[self.first])
        for operation in ("stop", "clear", "delete", "schedule"):
            with self.subTest(operation=operation):
                atomic_write_json(self.path, baseline)
                self.action(lambda s: family.medicine_action(s, self.mom, operation, NOW,
                            payload={"doseTimes": ["18:00"]} if operation == "schedule" else None, medicine_id=self.first), NOW)
                self.reject(event, "DESK_STALE_SCHEDULE")

    def test_skipped_and_missed_current_dose_are_not_overwritten(self):
        baseline = self.state()
        event = self.packet(medicines=[self.first])
        for state in ("SKIPPED", "MISSED"):
            changed = deepcopy(baseline)
            changed["profiles"][self.mom]["store"]["doses"][event["doseIds"][0]]["state"] = state
            atomic_write_json(self.path, changed)
            self.reject(event, "DESK_STALE_SCHEDULE")

    def test_already_chat_taken_preserves_original_source_and_timestamp(self):
        event = self.packet(medicines=[self.first])
        at = NOW - timedelta(minutes=1)
        self.action(lambda s: family.mark(s, self.mom, at, "chat", event["key"], [self.first], voice=read_json(VOICE_PATH)), at)
        result = self.receive(event)
        record = self.state()["profiles"][self.mom]["store"]["doses"][event["doseIds"][0]]
        self.assertEqual(record["source"], "chat")
        self.assertEqual(record["takenAt"], "2026-11-01T17:04:00+09:00")
        self.assertFalse(result["notify"])
        self.assertEqual(result["changedDoseIds"], [])

    def test_press_and_delivery_time_boundaries(self):
        baseline = self.state()
        event = self.packet()
        cases = [(NOW, NOW + timedelta(seconds=600), True), (NOW, NOW + timedelta(seconds=601), False),
                 (NOW, NOW - timedelta(seconds=120), True), (NOW, NOW - timedelta(seconds=121), False),
                 (parse_instant("2026-11-01T16:00:00+09:00"), None, True),
                 (parse_instant("2026-11-01T15:59:59+09:00"), None, False),
                 (parse_instant("2026-11-01T18:30:00+09:00"), None, True),
                 (parse_instant("2026-11-01T18:30:01+09:00"), None, False)]
        for pressed, now, valid in cases:
            with self.subTest(pressed=pressed, now=now):
                atomic_write_json(self.path, baseline)
                packet = {**event, "pressedAt": pressed.isoformat()}
                if valid:
                    self.assertTrue(self.receive(packet, now=now or pressed)["recorded"])
                else:
                    self.reject(packet, "DESK_TIME_WINDOW", now=now or pressed)

    def test_write_failure_keeps_both_dose_and_receipt_unchanged(self):
        event = self.packet()
        before = self.path.read_bytes()
        with patch("tools.family_store.atomic_write_json", side_effect=ToolError("WRITE_FAILED", "fixture")), self.assertRaises(ToolError):
            self.receive(event)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.state()["profiles"][self.mom]["deskRequests"], {})

    def test_press_before_schedule_confirmation_is_rejected(self):
        confirmed = parse_instant("2026-11-01T16:59:00+09:00")
        self.action(lambda s: family.medicine_action(s, self.mom, "schedule", confirmed,
                    {"doseTimes": ["17:00"], "dosePerTime": 2}, self.first), confirmed)
        event = self.packet(medicines=[self.first])
        event["pressedAt"] = "2026-11-01T16:58:00+09:00"
        self.reject(event, "DESK_TIME_WINDOW", now=confirmed)

    def test_existing_v3_without_optional_ledger_remains_compatible(self):
        state = self.state()
        for profile in state["profiles"].values():
            profile.pop("deskRequests", None)
        atomic_write_json(self.path, state)
        self.assertTrue(self.receive()["recorded"])

    def test_unknown_profile_and_damaged_receipt_are_errors(self):
        event = self.packet()
        unknown = "profile_unknown"
        altered = identify({**event, "profileId": unknown, "key": event["key"].replace(self.mom, unknown)})
        self.reject(altered, "PROFILE_NOT_FOUND", bound=unknown)
        state = self.state()
        state["profiles"][self.mom]["deskRequests"] = {"desk_" + "0" * 64: {"digest": [], "result": {}}}
        atomic_write_json(self.path, state)
        self.reject(event, "INVALID_STORE")

    def command(self, source, now="2026-11-01T17:05:00+09:00"):
        return [sys.executable, "-S", "-B", str(ROOT / "tools/desk_event.py"), "--profile", self.mom,
                "--store", str(self.path), "--in", str(source), "--now", now]

    def test_cli_is_single_json_without_site_packages_and_does_not_read_env(self):
        source = self.directory / "event.json"
        atomic_write_json(source, self.packet())
        result = subprocess.run(self.command(source), cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(json.loads(result.stdout)["recorded"])

    def test_concurrent_identical_requests_record_once(self):
        source = self.directory / "event.json"
        atomic_write_json(source, self.packet())
        processes = [subprocess.Popen(self.command(source), cwd=self.directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8") for _ in range(2)]
        values = [p.communicate(timeout=15) for p in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), values)
        results = [json.loads(output) for output, error in values]
        self.assertEqual(sum(result["notify"] for result in results), 1)
        self.assertEqual(sum(result["replayed"] for result in results), 1)
        self.assertEqual(len(self.state()["profiles"][self.mom]["deskRequests"]), 1)

    def test_concurrent_subset_requests_preserve_both_records(self):
        processes = []
        for medicine in (self.first, self.second):
            source = self.directory / (medicine + ".json")
            atomic_write_json(source, self.packet(medicines=[medicine]))
            processes.append(subprocess.Popen(self.command(source), cwd=self.directory, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, text=True, encoding="utf-8"))
        outputs = [process.communicate(timeout=15) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), outputs)
        self.assertTrue(all(json.loads(output)["notify"] for output, error in outputs))
        self.assertEqual(len(self.state()["profiles"][self.mom]["deskRequests"]), 2)

    def test_input_rejects_duplicate_json_fields_nonfinite_and_large_payload(self):
        source = self.directory / "invalid.json"
        for raw in ('{"source":"desk","source":"chat"}', '{"value":NaN}', '"' + 'x' * 65536 + '"', '[' * 1500 + '0' + ']' * 1500):
            source.write_text(raw, encoding="utf-8")
            with self.assertRaises(ToolError) as caught:
                read_event(source)
            self.assertEqual(caught.exception.code, "INVALID_DESK_EVENT")

    def test_known_bridge_wire_format_and_request_hash_vector(self):
        value = read_event(ROOT / "fixtures/desk_event.json")
        normalized, at, pressed = validate_event(value, "profile_demo")
        self.assertEqual(normalized, value)
        self.assertEqual(at, parse_instant("2026-11-01T17:00:00+09:00"))
        self.assertEqual(pressed, NOW)

    def test_receiver_never_creates_or_migrates_a_store(self):
        event = self.packet()
        for raw in (None, med_store.empty_store()):
            path = self.directory / ("missing.json" if raw is None else "v2.json")
            if raw is not None:
                atomic_write_json(path, raw)
                before = path.read_bytes()
            with self.assertRaises(ToolError) as caught:
                family.transact(path, lambda s: accept(s, event, self.mom, NOW), NOW, require_existing=True)
            self.assertEqual(caught.exception.code, "STORE_NOT_READY")
            if raw is None:
                self.assertFalse(path.exists())
            else:
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
