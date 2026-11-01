from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.common import ToolError, atomic_write_json, parse_instant, read_json, run_cli
from tools import dose_state, family_store as family, intake, notification_images as images


ROOT = Path(__file__).resolve().parents[1]
AT = parse_instant("2026-11-01T13:00:00+09:00")
KEY = "2026-11-01|17:00"


def medicine(code):
    return {"items": [{"name": "합성시연정" + code, "itemSeq": code, "dosePerTime": 1,
                       "startDate": "2026-11-01", "durationDays": 5, "doseTimes": ["17:00"], "confirmedAt": AT.isoformat()}]}


class NotificationImageTests(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("Network disabled"))
        guard.start()
        self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = self.root / "family" / "medication_store.json"
        self.schedule, self.doses = self.root / "schedule.json", self.root / "doses.json"
        atomic_write_json(self.schedule, read_json(ROOT / "fixtures/notification_schedule.json"))

    def v1(self, command, *args, with_images=True):
        return dose_state.main([command, *args, "--schedule", str(self.schedule), "--doses", str(self.doses),
                                *(["--with-images"] if with_images else [])])

    def make_family(self):
        profile = family.transact(self.store, lambda s: family.create_profile(s, "시연대상", AT), AT)["profileId"]
        other = family.transact(self.store, lambda s: family.create_profile(s, "다른가족", AT), AT)["profileId"]
        ids = []
        for code in ("900001", "900002"):
            result = family.transact(self.store, lambda s: family.medicine_action(s, profile, "add", AT, medicine(code)), AT)
            ids.append(result["added"][0])
        family.transact(self.store, lambda s: family.medicine_action(s, other, "add", AT, medicine("900003")), AT)
        return profile, other, ids

    def family_cli(self, command, profile, *args):
        return dose_state.main([command, "--store", str(self.store), "--profile", profile, *args, "--with-images"])

    def catalog_copy(self):
        shutil.copytree(ROOT / "config", self.root / "config")
        shutil.copytree(ROOT / "assets/notifications", self.root / "assets/notifications")
        return self.root / "config/notification_images.json"

    def test_all_four_shipped_pngs_are_distinct_verified_and_offline(self):
        catalog = images.load_catalog()
        self.assertEqual(set(catalog), {"step1", "step2", "step3", "taken"})
        self.assertEqual(len({item["sha256"] for item in catalog.values()}), 4)
        for item in catalog.values():
            self.assertTrue(Path(item["path"]).is_absolute())
            self.assertTrue(Path(item["path"]).is_file())
            self.assertEqual(item["mimeType"], "image/png")
            self.assertNotIn("profile_", item["alt"])

    def test_three_stages_attach_the_matching_face_and_repeated_poll_is_silent(self):
        for time, expected in (("17:05", "step1"), ("17:12", "step2"), ("17:31", "step3")):
            result = self.v1("due", "--now", "2026-11-01T" + time + ":00+09:00")
            self.assertEqual(result["send"][0]["image"]["id"], expected)
            self.assertEqual(self.v1("due", "--now", "2026-11-01T" + time + ":00+09:00")["send"], [])
        self.assertNotIn("image", self.doses.read_text(encoding="utf-8"))

    def test_first_late_poll_only_attaches_current_phase(self):
        result = self.v1("due", "--now", "2026-11-01T17:12:00+09:00")
        self.assertEqual([m["image"]["id"] for m in result["send"]], ["step2"])

    def test_missed_and_revisit_are_text_only(self):
        result = self.v1("due", "--now", "2026-11-01T18:31:00+09:00")
        self.assertEqual(result["send"][0]["step"], 4)
        self.assertNotIn("image", result["send"][0])
        result = self.v1("due", "--now", "2026-11-02T09:00:00+09:00")
        self.assertEqual(result["send"][0]["key"], "revisit|2026-11-02")
        self.assertNotIn("image", result["send"][0])

    def test_quiet_hours_and_already_skipped_occurrences_do_not_attach(self):
        schedule = read_json(self.schedule)
        schedule["occurrences"] = [{"key": "2026-11-01|23:00", "at": "2026-11-01T23:00:00+09:00"}]
        atomic_write_json(self.schedule, schedule)
        self.assertEqual(self.v1("due", "--now", "2026-11-01T23:12:00+09:00")["send"], [])
        schedule["occurrences"] = [{"key": "2026-11-01|07:00", "at": "2026-11-01T07:00:00+09:00"}]
        atomic_write_json(self.schedule, schedule)
        self.assertEqual(self.v1("due", "--now", "2026-11-01T17:05:00+09:00")["send"], [])

    def test_first_mark_smiles_duplicate_preserves_record_without_another_image(self):
        first = self.v1("mark", KEY, "--source", "chat", "--at", "2026-11-01T17:05:00+09:00")
        self.assertEqual(first["image"]["id"], "taken")
        before = self.doses.read_bytes()
        again = self.v1("mark", KEY, "--source", "chat", "--at", "2026-11-01T17:12:00+09:00")
        self.assertNotIn("image", again)
        self.assertEqual(self.doses.read_bytes(), before)
        self.assertEqual(self.v1("due", "--now", "2026-11-01T17:31:00+09:00")["send"], [])

    def test_mark_without_a_target_and_desk_source_do_not_smile(self):
        self.assertEqual(self.v1("mark", "--source", "chat", "--at", "2026-11-01T15:00:00+09:00"), {"marked": None})
        result = self.v1("mark", KEY, "--source", "desk", "--at", "2026-11-01T17:05:00+09:00")
        self.assertEqual(result["state"], "TAKEN")
        self.assertNotIn("image", result)

    def test_v1_revisit_metadata_does_not_break_later_mark(self):
        self.v1("due", "--now", "2026-11-02T09:00:00+09:00")
        result = self.v1("mark", KEY, "--source", "chat", "--at", "2026-11-02T09:01:00+09:00")
        self.assertEqual(result["image"]["id"], "taken")

    def test_family_due_keeps_profile_identity_and_each_profile_face(self):
        profile, other, _ = self.make_family()
        result = dose_state.main(["due", "--store", str(self.store), "--now", "2026-11-01T17:12:00+09:00", "--with-images"])
        self.assertEqual({m["profileId"] for m in result["send"]}, {profile, other})
        self.assertTrue(all(m["image"]["id"] == "step2" for m in result["send"]))
        self.assertTrue(all(m["key"].startswith(m["profileId"] + "|") for m in result["send"]))

    def test_partial_mark_has_no_full_smile_last_mark_smiles_only_once(self):
        profile, other, ids = self.make_family()
        first = self.family_cli("mark", profile, profile + "|" + KEY, "--medicine-id", ids[0], "--source", "chat", "--at", "2026-11-01T17:05:00+09:00")
        self.assertNotIn("image", first)
        final = self.family_cli("mark", profile, profile + "|" + KEY, "--medicine-id", ids[1], "--source", "chat", "--at", "2026-11-01T17:06:00+09:00")
        self.assertEqual(final["image"]["id"], "taken")
        again = self.family_cli("mark", profile, profile + "|" + KEY, "--source", "chat", "--at", "2026-11-01T17:07:00+09:00")
        self.assertNotIn("image", again)
        pending = self.family_cli("due", other, "--now", "2026-11-01T17:12:00+09:00")
        self.assertEqual(pending["send"][0]["image"]["id"], "step2")

    def test_skipped_medicine_is_not_treated_as_all_taken(self):
        profile, _, ids = self.make_family()
        dose_state.main(["skip", profile + "|" + KEY, "--profile", profile, "--store", str(self.store), "--medicine-id", ids[0], "--at", "2026-11-01T17:05:00+09:00"])
        result = self.family_cli("mark", profile, profile + "|" + KEY, "--medicine-id", ids[1], "--source", "chat", "--at", "2026-11-01T17:06:00+09:00")
        self.assertNotIn("image", result)

    def test_stopped_and_deleted_medicines_do_not_create_image_notifications(self):
        profile, other, ids = self.make_family()
        for operation, med in (("stop", ids[0]), ("delete", ids[1])):
            family.transact(self.store, lambda s: family.medicine_action(s, profile, operation, AT, medicine_id=med), AT)
        self.assertEqual(self.family_cli("due", profile, "--now", "2026-11-01T17:12:00+09:00")["send"], [])
        self.assertEqual(self.family_cli("due", other, "--now", "2026-11-01T17:12:00+09:00")["send"][0]["image"]["id"], "step2")

    def test_appointment_reminders_never_use_a_crying_face(self):
        profile, _, _ = self.make_family()
        payload = {"title": "시연 방문", "start": "2026-11-09T15:00:00+09:00", "end": "2026-11-09T16:00:00+09:00"}
        family.transact(self.store, lambda s: family.appointment_action(s, profile, "add", AT, payload), AT)
        result = self.family_cli("due", profile, "--now", "2026-11-02T09:00:00+09:00")
        visits = [m for m in result["send"] if m.get("kind") == "appointment"]
        self.assertEqual(len(visits), 1)
        self.assertTrue(all("image" not in m for m in result["send"]))

    def test_intake_proposal_does_not_smile_confirmation_does_and_replay_does_not(self):
        profile, _, _ = self.make_family()
        request = self.root / "request.json"
        atomic_write_json(request, {"source": "voice", "profileId": profile, "requestId": "image-confirm-1",
                                   "action": "dose.mark", "payload": {"key": profile + "|" + KEY}})
        proposed = intake.main(["propose", "--in", str(request), "--store", str(self.store), "--profile", profile, "--at", "2026-11-01T17:05:00+09:00"])
        self.assertNotIn("image", proposed)
        self.assertFalse(any(d["state"] == "TAKEN" for d in read_json(self.store)["profiles"][profile]["store"]["doses"].values()))
        command = ["confirm", proposed["draftId"], "--profile", profile, "--store", str(self.store), "--accept", "--with-images", "--at", "2026-11-01T17:06:00+09:00"]
        self.assertEqual(intake.main(command)["image"]["id"], "taken")
        self.assertNotIn("image", read_json(self.store)["drafts"][proposed["draftId"]]["result"])
        self.assertNotIn("image", intake.main(command))

    def test_no_option_does_not_load_assets_or_change_legacy_output(self):
        with patch.object(dose_state, "load_catalog", side_effect=AssertionError("Opt in only")):
            result = self.v1("due", "--now", "2026-11-01T17:12:00+09:00", with_images=False)
        self.assertNotIn("image", result["send"][0])

    def test_missing_image_fails_before_consuming_notification_or_creating_store(self):
        self.catalog_copy()
        (self.root / "assets/notifications/step1-teary-v1.png").unlink()
        with patch.object(images, "ROOT", self.root):
            with self.assertRaises(ToolError) as caught:
                self.v1("due", "--now", "2026-11-01T17:12:00+09:00")
        self.assertEqual(caught.exception.code, "INVALID_IMAGE_ASSET")
        self.assertFalse(self.doses.exists())

    def test_tampered_png_and_manifest_path_escape_are_rejected(self):
        manifest_path = self.catalog_copy()
        manifest = read_json(manifest_path)
        manifest["images"]["step1"]["file"] = "../../.env"
        atomic_write_json(manifest_path, manifest)
        with self.assertRaises(ToolError):
            images.load_catalog(self.root)
        manifest["images"]["step1"]["file"] = "step1-teary-v1.png"
        atomic_write_json(manifest_path, manifest)
        image_path = self.root / "assets/notifications/step1-teary-v1.png"
        image_path.write_bytes(b"fixture-private-body")
        with self.assertRaises(ToolError) as caught:
            images.load_catalog(self.root)
        self.assertEqual(caught.exception.code, "INVALID_IMAGE_ASSET")
        self.assertNotIn("fixture-private-body", caught.exception.message)

    def test_write_failure_never_returns_a_success_image_or_changes_the_record(self):
        self.v1("due", "--now", "2026-11-01T17:05:00+09:00")
        before = self.doses.read_bytes()
        output = io.StringIO()
        with patch("tools.common.os.replace", side_effect=OSError("fixture-private-error")), redirect_stdout(output):
            code = run_cli(lambda: self.v1("mark", KEY, "--source", "chat", "--at", "2026-11-01T17:06:00+09:00"))
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("error", result)
        self.assertNotIn("image", result)
        self.assertEqual(self.doses.read_bytes(), before)

    def test_attachment_decoration_does_not_mutate_the_original_notification(self):
        original = {"now": AT.isoformat(), "send": [{"key": KEY, "step": 1, "say": "합성 알림"}]}
        before = deepcopy(original)
        self.assertIn("image", images.attach_due(original, images.load_catalog())["send"][0])
        self.assertEqual(original, before)

    def test_cli_runs_without_site_packages_and_returns_one_json(self):
        process = subprocess.run([sys.executable, "-S", "-B", str(ROOT / "tools/dose_state.py"), "due",
                                  "--schedule", str(self.schedule), "--doses", str(self.doses),
                                  "--now", "2026-11-01T17:12:00+09:00", "--with-images"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "")
        self.assertEqual(len(process.stdout.splitlines()), 1)
        self.assertEqual(json.loads(process.stdout)["send"][0]["image"]["id"], "step2")


if __name__ == "__main__":
    unittest.main()
