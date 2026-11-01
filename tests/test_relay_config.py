from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from types import SimpleNamespace
import urllib.error

from tools.common import ToolError, run_cli
from tools import dur_lookup, relay_config, rx_lookup
from tools.dur_rules import DUR_PATHS
from tools.rx_relay import RelayClient


ROOT = Path(__file__).resolve().parents[1]
PAIR = {"PUBLIC_DATA_RELAY_URL": "https://relay.example.invalid",
        "PUBLIC_DATA_RELAY_TOKEN": "fixture-relay-token-0123456789012345678901"}
OTHER_PAIR = {"PUBLIC_DATA_RELAY_URL": "https://other.example.invalid",
              "PUBLIC_DATA_RELAY_TOKEN": "fixture-other-token-0123456789012345678901"}
READ = {"readable": True, "medicines": [{"readNo": 1, "readName": "가상연동정200밀리그램"}]}
ROW = json.loads((ROOT / "fixtures/permit_code_field.json").read_text(encoding="utf-8"))["body"]["items"][0]


def response(value):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = json.dumps(value).encode()
    return result


class RelayConfigTests(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("Network disabled"))
        guard.start()
        self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env_patch = patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def error_code(self, expected, operation):
        with self.assertRaises(ToolError) as caught:
            operation()
        self.assertEqual(caught.exception.code, expected)
        self.assertNotIn(PAIR["PUBLIC_DATA_RELAY_TOKEN"], caught.exception.message)
        self.assertNotIn(PAIR["PUBLIC_DATA_RELAY_URL"], caught.exception.message)

    def save(self):
        return relay_config.save_connection(self.root, PAIR)

    def client(self, environ=None):
        return rx_lookup.create_client({} if environ is None else environ, root=self.root)

    def test_saved_pair_survives_fresh_process_with_no_lookup_environment(self):
        self.save()
        probe = """import json, sys
from tools.rx_lookup import create_client
from tools.rx_relay import RelayClient
client = create_client({}, root=sys.argv[1])
print(json.dumps({'relay': isinstance(client, RelayClient), 'hasToken': bool(client.token)}))
"""
        process = subprocess.run([sys.executable, "-c", probe, str(self.root)], cwd=self.root,
                                 env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), {"relay": True, "hasToken": True})
        self.assertNotIn(PAIR["PUBLIC_DATA_RELAY_TOKEN"], process.stdout + process.stderr)

    def test_lookup_and_explanation_cli_use_saved_pair_without_shell_wrapper(self):
        self.save()
        source, target = self.root / "read.json", self.root / "candidates.json"
        source.write_text(json.dumps(READ), encoding="utf-8")
        opener = Mock()
        opener.open.return_value = response({"items": [ROW]})
        with patch.object(rx_lookup, "ROOT", self.root), patch("urllib.request.build_opener", return_value=opener):
            result = rx_lookup.main(["--in", str(source), "--out", str(target)])
            self.assertEqual(result["cards"][0]["status"], "OK")
            opener.open.return_value = response({"items": [{"itemSeq": "100001", "itemName": "합성정", "updateDe": "2026-11-01"}]})
            detail = rx_lookup.main(["--explain", "100001"])
        self.assertEqual(detail["status"], "OK")
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual([json.loads(request.data)["operation"] for request in requests], ["permit", "explain"])
        for request in requests:
            self.assertEqual(request.full_url, PAIR["PUBLIC_DATA_RELAY_URL"] + "/v1/query")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + PAIR["PUBLIC_DATA_RELAY_TOKEN"])

    def test_dur_cli_uses_the_same_saved_pair(self):
        self.save()
        opener = Mock()
        opener.open.return_value = response({"items": [], "totalCount": 0, "pageNo": 1, "numOfRows": 100})
        snapshot = {"profileId": "profile_fixture", "profileAlias": "시연", "clinical": {},
                    "items": [{"name": "합성A정", "itemSeq": "900001"}, {"name": "합성B정", "itemSeq": "900002"}]}
        with patch.object(rx_lookup, "ROOT", self.root), patch("urllib.request.build_opener", return_value=opener), \
                patch.object(dur_lookup, "transact", return_value=snapshot):
            result = dur_lookup.main(["--store", str(self.root / "unused.json"), "--now", "2026-11-01T16:00:00+09:00"])
        self.assertEqual(result["lookup"], "ALL")
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(len(requests), 8)
        self.assertEqual({json.loads(request.data)["type"] for request in requests}, set(DUR_PATHS))
        self.assertTrue(all(request.full_url == PAIR["PUBLIC_DATA_RELAY_URL"] + "/v1/query" for request in requests))

    def test_empty_or_matching_partial_environment_uses_whole_saved_pair(self):
        self.save()
        for env in ({}, {"PUBLIC_DATA_RELAY_URL": "", "PUBLIC_DATA_RELAY_TOKEN": ""},
                    {"PUBLIC_DATA_RELAY_TOKEN": PAIR["PUBLIC_DATA_RELAY_TOKEN"]},
                    {"PUBLIC_DATA_RELAY_URL": PAIR["PUBLIC_DATA_RELAY_URL"] + "/"}):
            client = self.client(env)
            self.assertIsInstance(client, RelayClient)
            self.assertEqual(client.url, PAIR["PUBLIC_DATA_RELAY_URL"])
            self.assertEqual(client.token, PAIR["PUBLIC_DATA_RELAY_TOKEN"])

    def test_conflicting_environment_never_mixes_pairs_or_uses_direct_key(self):
        self.save()
        for env in (OTHER_PAIR, {"PUBLIC_DATA_RELAY_URL": OTHER_PAIR["PUBLIC_DATA_RELAY_URL"]},
                    {"PUBLIC_DATA_RELAY_TOKEN": OTHER_PAIR["PUBLIC_DATA_RELAY_TOKEN"]}):
            with patch.object(rx_lookup, "PublicDataClient") as direct:
                self.error_code("RELAY_CONFIG_CONFLICT", lambda: self.client({**env, "PUBLIC_DATA_SERVICE_KEY": "fixture-only"}))
                direct.assert_not_called()

    def test_explicit_pair_replacement_is_atomic_and_idempotent(self):
        self.assertTrue(self.save()["changed"])
        path = relay_config.config_path(self.root)
        before = path.read_bytes()
        self.assertFalse(self.save()["changed"])
        self.assertEqual(path.read_bytes(), before)
        self.error_code("RELAY_CONFIG_EXISTS", lambda: relay_config.save_connection(self.root, OTHER_PAIR))
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(relay_config.save_connection(self.root, OTHER_PAIR, replace=True)["changed"])
        self.assertEqual(self.client().url, OTHER_PAIR["PUBLIC_DATA_RELAY_URL"])
        self.assertEqual(self.client().token, OTHER_PAIR["PUBLIC_DATA_RELAY_TOKEN"])

    def test_invalid_replacement_preserves_previous_pair(self):
        self.save()
        before = relay_config.config_path(self.root).read_bytes()
        for bad in ({}, {"PUBLIC_DATA_RELAY_URL": PAIR["PUBLIC_DATA_RELAY_URL"]},
                    {**PAIR, "PUBLIC_DATA_RELAY_TOKEN": "short"},
                    {**PAIR, "PUBLIC_DATA_RELAY_URL": "https://user:secret@example.invalid"}):
            with self.assertRaises(ToolError):
                relay_config.save_connection(self.root, bad, replace=True)
            self.assertEqual(relay_config.config_path(self.root).read_bytes(), before)

    def test_failed_atomic_replace_preserves_old_file_and_removes_temporary(self):
        self.save()
        path = relay_config.config_path(self.root)
        before = path.read_bytes()
        with patch("tools.common.os.replace", side_effect=OSError("fixture-secret")):
            self.error_code("WRITE_FAILED", lambda: relay_config.save_connection(self.root, OTHER_PAIR, replace=True))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_missing_saved_file_is_a_pinned_error_with_no_direct_fallback(self):
        self.save()
        relay_config.config_path(self.root).unlink()
        with patch.object(rx_lookup, "PublicDataClient") as direct:
            self.error_code("RELAY_CONFIG_MISSING", lambda: self.client({"PUBLIC_DATA_SERVICE_KEY": "fixture-key"}))
            direct.assert_not_called()

    def test_corrupt_or_oversized_config_fails_without_echoing_contents(self):
        self.save()
        path = relay_config.config_path(self.root)
        for raw in (PAIR["PUBLIC_DATA_RELAY_TOKEN"], "[1]", '{"schemaVersion":1,"schemaVersion":1}',
                    "{}", '{"mode":NaN}', "x" * (relay_config.MAX_CONFIG + 1),
                    json.dumps({"schemaVersion": True, "mode": "relay", "url": PAIR["PUBLIC_DATA_RELAY_URL"], "token": PAIR["PUBLIC_DATA_RELAY_TOKEN"]})):
            path.write_text(raw, encoding="utf-8")
            self.error_code("INVALID_RELAY_CONFIG", self.client)

    def test_env_and_medical_state_are_unchanged_and_not_copied_into_settings(self):
        env = self.root / ".env"
        env.write_text("PUBLIC_DATA_SERVICE_KEY=fixture-government-key\n", encoding="utf-8")
        state = self.root / "state" / "medication_store.json"
        state.parent.mkdir()
        state.write_text('{"fixture":"preserve"}', encoding="utf-8")
        before = (env.read_bytes(), state.read_bytes())
        self.save()
        self.client()
        self.assertEqual((env.read_bytes(), state.read_bytes()), before)
        self.assertNotIn("fixture-government-key", relay_config.config_path(self.root).read_text(encoding="utf-8"))

    def test_saved_mode_does_not_read_stale_dotenv(self):
        self.save()
        loader = Mock(side_effect=AssertionError("Must not merge stale .env relay values"))
        settings, source = relay_config.resolve_settings(self.root, {}, loader)
        self.assertEqual(source, "saved")
        self.assertEqual(settings, PAIR)
        loader.assert_not_called()

    def test_status_and_save_cli_emit_only_redacted_json(self):
        with patch.object(relay_config, "ROOT", self.root), patch.dict(os.environ, PAIR, clear=True):
            for args in (["save"], ["status"]):
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    exit_code = run_cli(lambda: relay_config.main(args))
                self.assertEqual(exit_code, 0)
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                self.assertTrue(json.loads(output.getvalue())["configured"])
                self.assertEqual(errors.getvalue(), "")
                for secret in PAIR.values():
                    self.assertNotIn(secret, output.getvalue())

    def test_unconfigured_direct_no_key_and_read_only_dotenv_compatibility(self):
        self.assertIsInstance(self.client(), rx_lookup.PublicDataClient)
        self.assertEqual(rx_lookup.lookup(READ, self.client())["cards"][0]["error"]["code"], "NO_KEY")
        env = self.root / ".env"
        original = "\n".join(name + "=" + value for name, value in PAIR.items())
        env.write_text(original, encoding="utf-8")
        self.assertIsInstance(self.client(), RelayClient)
        self.assertEqual(env.read_text(encoding="utf-8"), original)
        self.assertFalse(relay_config.config_path(self.root).parent.exists())

    def test_incomplete_environment_without_saved_config_keeps_existing_error(self):
        for env, expected in (({"PUBLIC_DATA_RELAY_TOKEN": PAIR["PUBLIC_DATA_RELAY_TOKEN"]}, "INVALID_RELAY_URL"),
                              ({"PUBLIC_DATA_RELAY_URL": PAIR["PUBLIC_DATA_RELAY_URL"]}, "NO_RELAY_TOKEN")):
            client = self.client({**env, "PUBLIC_DATA_SERVICE_KEY": "fixture-government-key"})
            self.assertIsInstance(client, RelayClient)
            self.assertEqual(rx_lookup.lookup(READ, client)["cards"][0]["error"]["code"], expected)

    def test_saved_relay_flag_blocks_direct_mode_even_after_whole_state_is_lost(self):
        with patch.object(rx_lookup, "ROOT", self.root), patch.object(rx_lookup, "PublicDataClient") as direct:
            for env in ({"PUBLIC_DATA_SERVICE_KEY": "fixture-government-key"}, PAIR):
                with patch.dict(os.environ, env, clear=True):
                    self.error_code("RELAY_CONFIG_MISSING", lambda: rx_lookup.main(["--explain", "100001", "--saved-relay"]))
            direct.assert_not_called()

    def test_config_failure_cli_is_one_safe_json_and_nonzero(self):
        self.save()
        relay_config.config_path(self.root).write_text(PAIR["PUBLIC_DATA_RELAY_TOKEN"], encoding="utf-8")
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(rx_lookup, "ROOT", self.root), redirect_stdout(output), redirect_stderr(errors):
            code = run_cli(lambda: rx_lookup.main(["--explain", "100001", "--saved-relay"]))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "INVALID_RELAY_CONFIG")
        self.assertEqual(errors.getvalue(), "")
        self.assertNotIn(PAIR["PUBLIC_DATA_RELAY_TOKEN"], output.getvalue())

    def test_missing_dur_saved_config_stops_before_reading_or_migrating_any_store(self):
        with patch.object(rx_lookup, "ROOT", self.root), patch.object(dur_lookup, "transact") as transaction:
            self.error_code("RELAY_CONFIG_MISSING", lambda: dur_lookup.main(["--saved-relay", "--store", str(self.root / "absent.json")]))
        transaction.assert_not_called()
        self.assertFalse((self.root / "absent.json").exists())

    def test_posix_permission_policy_rejects_other_users_and_non_private_modes(self):
        policy_os = SimpleNamespace(name="posix", geteuid=lambda: 7)
        with patch.object(relay_config, "os", policy_os):
            for mode, owner in ((stat.S_IFREG | 0o644, 7), (stat.S_IFREG | 0o600, 8)):
                self.error_code("RELAY_CONFIG_PERMISSIONS", lambda: relay_config._check_private(SimpleNamespace(st_mode=mode, st_uid=owner)))
            relay_config._check_private(SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=7))

    def test_network_failure_in_saved_mode_never_falls_back_to_direct(self):
        self.save()
        client = self.client({"PUBLIC_DATA_SERVICE_KEY": "fixture-government-key"})
        client.opener, client.sleep = Mock(), Mock()
        client.opener.open.side_effect = urllib.error.URLError("fixture-secret")
        with patch.object(rx_lookup, "PublicDataClient") as direct:
            result = rx_lookup.lookup(READ, client)
            direct.assert_not_called()
        self.assertEqual(result["cards"][0]["error"]["code"], "RELAY_NETWORK_ERROR")
        self.assertEqual(client.opener.open.call_count, 2)

    def test_url_and_token_validation_rejects_control_characters_and_credentials(self):
        for url in ("https://relay.example.invalid\n", "https://relay.example.invalid/path", "https://relay.example.invalid?token=x",
                    "https://user:secret@relay.example.invalid", "http://relay.example.invalid", "https://relay.example.invalid\\evil"):
            self.error_code("INVALID_RELAY_URL", lambda: relay_config.save_connection(self.root, {**PAIR, "PUBLIC_DATA_RELAY_URL": url}))
        for token in (PAIR["PUBLIC_DATA_RELAY_TOKEN"] + "\r\n", None, 123, "x" * 201):
            self.error_code("NO_RELAY_TOKEN", lambda: relay_config.save_connection(self.root, {**PAIR, "PUBLIC_DATA_RELAY_TOKEN": token}))
        self.assertEqual(relay_config.validate_url("http://127.0.0.1:8765/"), "http://127.0.0.1:8765")

    @unittest.skipIf(os.name == "nt", "POSIX access bits; Windows uses profile ACLs")
    def test_private_permissions_are_enforced(self):
        self.save()
        path = relay_config.config_path(self.root)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        path.chmod(0o644)
        self.error_code("RELAY_CONFIG_PERMISSIONS", self.client)


if __name__ == "__main__":
    unittest.main()
