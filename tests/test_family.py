from copy import deepcopy
from datetime import timedelta
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
from tools import family_store as family
from tools import intake, med_store


ROOT = Path(__file__).resolve().parents[1]
AT = parse_instant("2026-11-01T13:00:00+09:00")
VOICE = read_json(VOICE_PATH)


def medicine(code="900001", name="가상A정"):
    return {"items": [{"name": name, "itemSeq": code, "dosePerTime": 1, "startDate": "2026-11-01",
                       "durationDays": 5, "doseTimes": ["17:00"], "confirmedAt": "2026-11-01T13:00:00+09:00"}]}


class FamilyCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state/medication_store.json"

    def run_action(self, action, at=AT, **kwargs):
        return family.transact(self.path, action, at, **kwargs)

    def state(self):
        return read_json(self.path)

    def profiles(self):
        mom = self.run_action(lambda s: family.create_profile(s, "엄마", AT))["profileId"]
        grandma = self.run_action(lambda s: family.create_profile(s, "할머니", AT))["profileId"]
        return mom, grandma

    def add(self, profile, value=None):
        return self.run_action(lambda s: family.medicine_action(s, profile, "add", AT, value or medicine()))["added"][0]

    def proposal(self, profile, action="medicine.add", payload=None, request="voice-1", replace=None):
        value = {"profileId": profile, "source": "voice", "requestId": request, "action": action,
                 "payload": medicine() if payload is None else payload}
        return self.run_action(lambda s: intake.propose(s, value, AT, replace_id=replace), include_changes=False)


class FamilyTests(FamilyCase):
    def test_same_official_product_is_allowed_for_different_profiles_only(self):
        mom, grandma = self.profiles()
        self.add(mom)
        self.add(grandma)
        before = self.path.read_bytes()
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: family.medicine_action(s, mom, "add", AT, medicine(), request_id="different"))
        self.assertEqual(caught.exception.code, "DUPLICATE_MEDICINE")
        self.assertEqual(self.path.read_bytes(), before)

    def test_multiple_profiles_require_explicit_target_and_never_guess_alias(self):
        mom, _ = self.profiles()
        for reference in (None, "어머니", [], "unknown"):
            with self.assertRaises(ToolError):
                self.run_action(lambda s, ref=reference: family.medicine_action(s, ref, "list", AT))
        self.assertEqual(self.run_action(lambda s: family.medicine_action(s, "엄마", "list", AT))["profileId"], mom)

    def test_mark_and_reminders_are_isolated_even_at_identical_clock_time(self):
        mom, grandma = self.profiles()
        mom_med, grandma_med = self.add(mom), self.add(grandma)
        now = parse_instant("2026-11-01T17:12:00+09:00")
        first = self.run_action(lambda s: family.due(s, now, VOICE), now)
        self.assertEqual({m["profileId"] for m in first["send"]}, {mom, grandma})
        self.assertEqual(len({m["key"] for m in first["send"]}), 2)
        self.assertEqual(self.run_action(lambda s: family.due(s, now, VOICE), now)["send"], [])
        self.run_action(lambda s: family.mark(s, mom, now, "chat", mom + "|2026-11-01|17:00", voice=VOICE), now)
        after = parse_instant("2026-11-01T17:31:00+09:00")
        send = self.run_action(lambda s: family.due(s, after, VOICE), after)["send"]
        self.assertEqual([m["profileId"] for m in send], [grandma])
        self.assertIn("[할머니]", send[0]["say"])
        records = self.state()["profiles"][mom]["store"]["doses"].values()
        self.assertTrue(any(d["state"] == "TAKEN" and d["recordedBy"] == "account_owner" for d in records))
        self.assertNotEqual(mom_med, grandma_med)

    def test_wrong_profile_medicine_or_scoped_key_is_rejected(self):
        mom, grandma = self.profiles()
        mom_med = self.add(mom)
        self.add(grandma)
        before = self.path.read_bytes()
        for action in (lambda s: family.medicine_action(s, grandma, "stop", AT, medicine_id=mom_med),
                       lambda s: family.mark(s, grandma, AT, "chat", mom + "|2026-11-01|17:00", voice=VOICE)):
            with self.assertRaises(ToolError):
                self.run_action(action)
            self.assertEqual(self.path.read_bytes(), before)

    def test_stop_clear_delete_never_change_other_profile(self):
        mom, grandma = self.profiles()
        mom_med = self.add(mom)
        self.add(grandma)
        unchanged = deepcopy(self.state()["profiles"][grandma])
        for operation in ("stop", "clear", "delete"):
            self.run_action(lambda s, op=operation: family.medicine_action(s, mom, op, AT, medicine_id=mom_med))
            self.assertEqual(self.state()["profiles"][grandma], unchanged)

    def test_calendar_keys_alias_and_fingerprints_are_profile_scoped(self):
        mom, grandma = self.profiles()
        self.add(mom)
        self.add(grandma)
        plan = self.run_action(family.calendar_plan)["changes"]
        mom_job = next(j for j in plan if j["key"] == mom + "|2026-11-01|17:00")
        other_job = next(j for j in plan if j["key"] == grandma + "|2026-11-01|17:00")
        self.assertTrue(mom_job["event"]["summary"].startswith("[엄마]"))
        self.assertNotEqual(mom_job["fingerprint"], other_job["fingerprint"])
        self.run_action(lambda s: family.acknowledge(s, mom_job["key"], mom_job["fingerprint"], "fixture-mom-event"))
        replay = self.run_action(lambda s: family.acknowledge(s, mom_job["key"], mom_job["fingerprint"], "fixture-mom-event"))
        self.assertTrue(replay["replayed"])
        self.run_action(lambda s: family.update_profile(s, mom, {"alias": "엄마 약"}, AT))
        new_job = next(j for j in self.run_action(family.calendar_plan)["changes"] if j["key"] == mom_job["key"])
        self.assertEqual(new_job["eventId"], "fixture-mom-event")
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: family.acknowledge(s, mom_job["key"], mom_job["fingerprint"], "fixture-mom-event"))
        self.assertEqual(caught.exception.code, "STALE_CALENDAR_CHANGE")

    def test_v2_migration_preserves_record_ids_taken_and_calendar_id_with_backup(self):
        old = med_store.empty_store()
        med_store.add_medicines(old, medicine(), AT)
        med_store.store_mark(old, AT, "chat", "2026-11-01|17:00", voice=VOICE)
        med_store.refresh_calendar(old, AT)
        key = "2026-11-01|17:00"
        pending = old["calendar"]["pending"][key]
        med_store.acknowledge_calendar(old, key, pending["fingerprint"], "fixture-existing-event")
        atomic_write_json(self.path, old)
        result = self.run_action(family.calendar_plan)
        nested = self.state()["profiles"]["profile_legacy"]["store"]
        self.assertEqual(nested["medicines"], old["medicines"])
        self.assertEqual(nested["doses"], old["doses"])
        job = next(j for j in result["changes"] if j["key"] == "profile_legacy|" + key)
        self.assertEqual(job["eventId"], "fixture-existing-event")
        self.assertEqual(job["legacyKey"], key)
        backup = next((self.path.parent / "backups").glob("*.json"))
        self.assertEqual(read_json(backup), old)

    def test_failed_migration_save_keeps_original_v2_file(self):
        old = med_store.empty_store()
        med_store.add_medicines(old, medicine(), AT)
        atomic_write_json(self.path, old)
        original = self.path.read_bytes()
        with patch("tools.family_store.atomic_write_json", side_effect=ToolError("WRITE_FAILED", "fixture")), self.assertRaises(ToolError):
            self.run_action(family.list_profiles)
        self.assertEqual(self.path.read_bytes(), original)

    def test_duplicate_alias_unknown_clinical_fields_and_revision_are_rejected(self):
        mom, _ = self.profiles()
        for action in (lambda s: family.create_profile(s, "엄마", AT),
                       lambda s: family.update_profile(s, mom, {"clinical": {"birthDate": "2000-01-01"}}, AT),
                       lambda s: family.update_profile(s, mom, {"clinical": {"ageYears": 70, "ageMonths": 840}}, AT)):
            with self.assertRaises(ToolError):
                self.run_action(action)
        with self.assertRaises(ToolError) as caught:
            self.run_action(family.list_profiles, expected_revision=0)
        self.assertEqual(caught.exception.code, "REVISION_CONFLICT")


class IntakeTests(FamilyCase):
    def test_replacement_cannot_cancel_another_family_profile_draft(self):
        mom, grandma = self.profiles()
        draft = self.proposal(grandma)
        before = self.path.read_bytes()
        with self.assertRaises(ToolError) as caught:
            self.proposal(mom, request="mom-request", replace=draft["draftId"])
        self.assertEqual(caught.exception.code, "PROFILE_MISMATCH")
        self.assertEqual(self.path.read_bytes(), before)

    def test_voice_proposal_does_not_save_medicine_until_real_commit_and_replay_is_safe(self):
        mom, _ = self.profiles()
        proposal = self.proposal(mom)
        self.assertEqual(self.state()["profiles"][mom]["store"]["medicines"], {})
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom))
        self.assertEqual(caught.exception.code, "CONFIRMATION_REQUIRED")
        first = self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom, True))
        second = self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom, True))
        self.assertEqual(first["added"], second["added"])
        self.assertTrue(second["replayed"])
        self.assertEqual(len(self.state()["profiles"][mom]["store"]["medicines"]), 1)

    def test_draft_wrong_profile_stale_revision_expiry_and_cancel_never_save(self):
        mom, grandma = self.profiles()
        proposal = self.proposal(mom)
        for when, reference, code in ((AT, grandma, "PROFILE_MISMATCH"), (AT + timedelta(minutes=15), mom, "DRAFT_EXPIRED")):
            with self.assertRaises(ToolError) as caught:
                self.run_action(lambda s, t=when, ref=reference: intake.confirm(s, proposal["draftId"], t, ref, True), when)
            self.assertEqual(caught.exception.code, code)
        self.run_action(lambda s: family.update_profile(s, grandma, {"alias": "할머니 약"}, AT))
        with self.assertRaises(ToolError) as caught:
            self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom, True))
        self.assertEqual(caught.exception.code, "REVISION_CONFLICT")
        self.run_action(lambda s: intake.cancel(s, proposal["draftId"], mom))
        with self.assertRaises(ToolError):
            self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom, True))
        self.assertEqual(self.state()["profiles"][mom]["store"]["medicines"], {})

    def test_correction_supersedes_old_draft_and_same_request_does_not_duplicate(self):
        mom, _ = self.profiles()
        first = self.proposal(mom)
        repeated = self.proposal(mom)
        self.assertEqual(first["draftId"], repeated["draftId"])
        changed = medicine()
        changed["items"][0]["doseTimes"] = ["18:30"]
        second = self.proposal(mom, payload=changed, request="voice-2", replace=first["draftId"])
        with self.assertRaises(ToolError):
            self.run_action(lambda s: intake.confirm(s, first["draftId"], AT, mom, True))
        self.run_action(lambda s: intake.confirm(s, second["draftId"], AT, mom, True))
        med = next(iter(self.state()["profiles"][mom]["store"]["medicines"].values()))
        self.assertEqual(med["schedule"]["doseTimes"], ["18:30"])

    def test_unknown_private_fields_invalid_source_and_unknown_action_are_rejected(self):
        mom, _ = self.profiles()
        value = medicine()
        value["items"][0]["patientName"] = "synthetic private"
        for action, payload in (("medicine.add", value), ("arbitrary.shell", {}),
                                ("medicine.stop", {"medicineId": []}),
                                ("appointment.add", {"title": "예약", "start": "2026-11-02T15:00:00+09:00", "end": "2026-11-02T14:00:00+09:00"})):
            with self.assertRaises(ToolError):
                self.proposal(mom, action, payload)
        self.assertEqual(self.state()["drafts"], {})

    def test_appointment_voice_add_update_cancel_and_existing_event_delete(self):
        mom, _ = self.profiles()
        value = {"title": "병원 예약", "start": "2026-11-02T15:00:00+09:00", "end": "2026-11-02T15:30:00+09:00"}
        proposal = self.proposal(mom, "appointment.add", value)
        self.assertEqual(self.state()["profiles"][mom]["appointments"], {})
        saved = self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom, True))
        appointment_id = saved["appointmentId"]
        job = saved["calendarChanges"][0]
        self.assertTrue(job["key"].startswith(mom + "|appointment|"))
        self.run_action(lambda s: family.acknowledge(s, job["key"], job["fingerprint"], "fixture-appointment"))
        proposal = self.proposal(mom, "appointment.update", {"appointmentId": appointment_id, "changes": {"allDayDate": "2026-11-03"}}, "voice-2")
        updated = self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom, True))
        self.assertEqual(updated["appointment"]["allDayDate"], "2026-11-03")
        proposal = self.proposal(mom, "appointment.cancel", {"appointmentId": appointment_id}, "voice-3")
        cancelled = self.run_action(lambda s: intake.confirm(s, proposal["draftId"], AT, mom, True))
        job = cancelled["calendarChanges"][0]
        self.assertEqual(job["operation"], "DELETE")
        self.assertEqual(job["eventId"], "fixture-appointment")


class AppointmentReminderTests(FamilyCase):
    def add_visit(self, profile, **extra):
        value = {"title": "병원 방문", "start": "2026-11-10T15:00:00+09:00", "end": "2026-11-10T16:00:00+09:00",
                 "caregiver": "엄마", **extra}
        return self.run_action(lambda s: family.appointment_action(s, profile, "add", AT, value))

    def notifications(self, when):
        at = parse_instant(when)
        return self.run_action(lambda s: family.due(s, at, VOICE), at)["send"]

    def test_week_and_day_before_at_nine_once_with_target_and_companion(self):
        _, grandma = self.profiles()
        result = self.add_visit(grandma)
        self.assertIn("동행: 엄마", result["calendarChanges"][0]["event"]["description"])
        self.assertEqual(self.notifications("2026-11-03T08:59:00+09:00"), [])
        first = self.notifications("2026-11-03T09:00:00+09:00")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["profileId"], grandma)
        self.assertIn("[할머니] 병원 방문 일정이 7일 뒤", first[0]["say"])
        self.assertIn("동행: 엄마", first[0]["say"])
        self.assertEqual(self.notifications("2026-11-03T09:01:00+09:00"), [])
        second = self.notifications("2026-11-09T09:00:00+09:00")
        self.assertEqual(len(second), 1)
        self.assertIn("1일 뒤", second[0]["say"])
        self.assertEqual(self.notifications("2026-11-09T12:00:00+09:00"), [])

    def test_reschedule_recomputes_days_and_cancel_silences_remaining_reminders(self):
        mom, grandma = self.profiles()
        visit = self.add_visit(grandma)["appointmentId"]
        new = {"start": "2026-11-12T15:00:00+09:00", "end": "2026-11-12T16:00:00+09:00"}
        with self.assertRaises(ToolError):
            self.run_action(lambda s: family.appointment_action(s, mom, "update", AT, new, visit))
        self.run_action(lambda s: family.appointment_action(s, grandma, "update", AT, new, visit))
        self.assertEqual(self.notifications("2026-11-03T09:00:00+09:00"), [])
        self.assertEqual(len(self.notifications("2026-11-05T09:00:00+09:00")), 1)
        at = parse_instant("2026-11-06T12:00:00+09:00")
        self.run_action(lambda s: family.appointment_action(s, grandma, "cancel", at, appointment_id=visit), at)
        self.assertEqual(self.notifications("2026-11-11T09:00:00+09:00"), [])

    def test_no_past_registration_catchup_and_quiet_hours_custom_and_all_day(self):
        _, grandma = self.profiles()
        self.add_visit(grandma, start="2026-11-08T15:00:00+09:00", end="2026-11-08T16:00:00+09:00")
        self.assertEqual(self.notifications("2026-11-01T14:00:00+09:00"), [])
        self.assertEqual(len(self.notifications("2026-11-07T09:00:00+09:00")), 1)
        # Separate calendar entry on the same profile, explicitly custom/quiet reminder time.
        payload = {"title": "동행 일정", "allDayDate": "2026-11-09", "reminderDays": [1], "reminderTime": "06:00"}
        self.run_action(lambda s: family.appointment_action(s, grandma, "add", AT, payload))
        self.assertEqual(self.notifications("2026-11-08T06:30:00+09:00"), [])
        send = self.notifications("2026-11-08T07:00:00+09:00")
        self.assertEqual(len(send), 1)
        self.assertIn("2026-11-09 종일", send[0]["say"])
        self.assertEqual(self.notifications("2026-11-08T23:30:00+09:00"), [])

    def test_invalid_reminder_fields_leave_original_store_and_empty_days_disable(self):
        _, grandma = self.profiles()
        before = self.path.read_bytes()
        for extra in ({"reminderDays": [1, 1]}, {"reminderDays": [True]}, {"reminderDays": [-1]},
                      {"reminderDays": [366]}, {"reminderDays": None}, {"reminderTime": "24:00"},
                      {"caregiver": {"name": "private"}}):
            with self.subTest(extra=extra), self.assertRaises(ToolError):
                self.add_visit(grandma, **extra)
            self.assertEqual(self.path.read_bytes(), before)
        self.add_visit(grandma, reminderDays=[])
        self.assertEqual(self.notifications("2026-11-03T09:00:00+09:00"), [])

    def test_due_uses_korean_date_for_utc_clock(self):
        _, grandma = self.profiles()
        self.add_visit(grandma)
        # 00:00 UTC is 09:00 Seoul on the reminder day.
        self.assertEqual(len(self.notifications("2026-11-03T00:00:00Z")), 1)


class FamilyCliTests(unittest.TestCase):
    def test_profiles_voice_draft_confirm_and_family_scoped_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env = {**os.environ, "PUBLIC_DATA_SERVICE_KEY": "", "PUBLIC_DATA_BASE_URL": "https://apis.data.go.kr", "PUBLIC_DATA_RELAY_URL": "", "PUBLIC_DATA_RELAY_TOKEN": ""}
            def call(script, *args, ok=True):
                result = subprocess.run([sys.executable, "-S", "-B", str(ROOT / "tools" / script), *args], cwd=directory, env=env,
                                        capture_output=True, text=True, encoding="utf-8", timeout=15)
                self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                return json.loads(result.stdout)
            created = call("profiles.py", "create", "--alias", "할머니")
            profile = created["profileId"]
            request = {"source": "voice", "profileId": profile, "action": "medicine.add", "requestId": "cli-voice-1", "payload": medicine()}
            source = directory / "intent.json"
            atomic_write_json(source, request)
            proposed = call("intake.py", "propose", "--in", str(source), "--at", "2026-11-01T13:00:00+09:00")
            committed = call("intake.py", "confirm", proposed["draftId"], "--profile", profile, "--accept", "--at", "2026-11-01T13:01:00+09:00")
            self.assertEqual(len(committed["added"]), 1)
            self.assertEqual(call("my_meds.py", "list", ok=False)["error"]["code"], "PROFILE_REQUIRED")
            self.assertEqual(len(call("my_meds.py", "list", "--profile", profile)["medicines"]), 1)
            self.assertEqual(call("dur_lookup.py", "--profile", profile)["lookup"], "FAILED")
            request.update(action="appointment.add", requestId="cli-calendar-1", payload={"title": "병원 방문", "caregiver": "엄마",
                           "start": "2026-11-10T15:00:00+09:00", "end": "2026-11-10T16:00:00+09:00"})
            atomic_write_json(source, request)
            proposed = call("intake.py", "propose", "--in", str(source), "--at", "2026-11-01T13:00:00+09:00")
            committed = call("intake.py", "confirm", proposed["draftId"], "--profile", profile, "--accept", "--at", "2026-11-01T13:01:00+09:00")
            visits = call("intake.py", "appointments", "--profile", profile)["appointments"]
            self.assertEqual(visits[0]["id"], committed["appointmentId"])
            self.assertEqual(visits[0]["caregiver"], "엄마")


if __name__ == "__main__":
    unittest.main()
