from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.error

from tools.common import ToolError, read_json
from tools.rx_lookup import EXPLAIN_PATH, PERMIT_PATH, explain, lookup, main as lookup_main
from tools.rx_relay import (ERRORS, MAX_BODY, RelayClient, RelayHandler, RelayServer,
                            _RelayHTTPConnection, _RelayHTTPSConnection, query_upstream)


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "fixture-relay-token-0123456789012345678901"
READ = {"readable": True, "medicines": [{"readNo": 1, "readName": "가상연동정200밀리그램", "readMaker": None}]}
ROW = read_json(ROOT / "fixtures/permit_code_field.json")["body"]["items"][0]


def wire_response(value):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(value).encode()
    return response


class OfflineCase(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("Network disabled in unit tests"))
        guard.start()
        self.addCleanup(guard.stop)


class RelayClientTests(OfflineCase):
    def client(self, value=None, env=None, effects=None):
        opener, sleeper = Mock(), Mock()
        opener.open.return_value = wire_response(value if value is not None else {"items": [ROW]})
        if effects is not None:
            opener.open.side_effect = effects
        client = RelayClient(env if env is not None else {"PUBLIC_DATA_RELAY_URL": "https://relay.example.invalid", "PUBLIC_DATA_RELAY_TOKEN": TOKEN}, opener, sleeper)
        return client, opener, sleeper

    def test_lookup_needs_only_relay_auth_and_never_sends_service_key(self):
        client, opener, _ = self.client(env={"PUBLIC_DATA_RELAY_URL": "https://relay.example.invalid",
                                           "PUBLIC_DATA_RELAY_TOKEN": TOKEN, "PUBLIC_DATA_SERVICE_KEY": "fixture-government-secret"})
        card = lookup(READ, client)["cards"][0]
        self.assertEqual(card["status"], "OK")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://relay.example.invalid/v1/query")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(json.loads(request.data), {"operation": "permit", "itemName": "가상연동정200밀리그램"})
        self.assertNotIn("fixture-government-secret", str(request.headers) + request.data.decode())
        self.assertNotIn(TOKEN, json.dumps(card))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2)

    def test_explanation_uses_same_output_contract(self):
        seed = read_json(ROOT / "fixtures/explanation.json")
        items = seed.get("body", seed.get("response", {}).get("body", {})).get("items")
        if isinstance(items, dict):
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
        client, opener, _ = self.client({"items": items})
        result = explain("100001", client)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["updateDe"], "2026-11-01")
        self.assertEqual(json.loads(opener.open.call_args.args[0].data), {"operation": "explain", "itemSeq": "100001"})

    def test_missing_token_fails_without_any_http(self):
        client, opener, _ = self.client(env={"PUBLIC_DATA_RELAY_URL": "https://relay.example.invalid"})
        self.assertEqual(lookup(READ, client)["cards"][0]["error"]["code"], "NO_RELAY_TOKEN")
        opener.open.assert_not_called()

    def test_insecure_non_loopback_or_credential_bearing_url_is_rejected(self):
        for url in ("http://relay.example.invalid", "https://user:password@relay.example.invalid", "https://relay.example.invalid/path",
                    "https://relay.example.invalid?key=fixture", "https://relay.example.invalid#fragment", "https://relay.example.invalid:bad", ""):
            client, opener, _ = self.client(env={"PUBLIC_DATA_RELAY_URL": url, "PUBLIC_DATA_RELAY_TOKEN": TOKEN})
            card = lookup(READ, client)["cards"][0]
            self.assertEqual(card["error"]["code"], "INVALID_RELAY_URL", url)
            opener.open.assert_not_called()

    def test_loopback_http_is_permitted_for_local_verification(self):
        client, opener, _ = self.client(env={"PUBLIC_DATA_RELAY_URL": "http://127.0.0.1:8765", "PUBLIC_DATA_RELAY_TOKEN": TOKEN})
        self.assertEqual(lookup(READ, client)["cards"][0]["status"], "OK")
        self.assertTrue(opener.open.call_args.args[0].full_url.startswith("http://127.0.0.1:8765/"))

    def test_network_retry_is_once_and_does_not_mutate_request(self):
        client, opener, sleeper = self.client(effects=[urllib.error.URLError("fixture secret"), wire_response({"items": []})])
        self.assertEqual(client.get(PERMIT_PATH, {"item_name": "合成"}), [])
        self.assertEqual(opener.open.call_count, 2)
        self.assertIs(opener.open.call_args_list[0].args[0], opener.open.call_args_list[1].args[0])
        sleeper.assert_called_once_with(0.5)

    def test_upstream_failure_is_safe_and_not_retried_again(self):
        body = {"error": {"code": "RELAY_UPSTREAM_API_ERROR", "message": "fixture-government-secret " + TOKEN}}
        error = urllib.error.HTTPError("https://relay.example.invalid/", 502, "fixture", {}, io.BytesIO(json.dumps(body).encode()))
        client, opener, sleeper = self.client(effects=[error])
        result = lookup(READ, client)
        self.assertEqual(result["cards"][0]["error"]["code"], "RELAY_UPSTREAM_API_ERROR")
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn("fixture-government-secret", json.dumps(result))
        self.assertEqual(opener.open.call_count, 1)
        sleeper.assert_not_called()

    def test_unknown_error_payload_and_invalid_success_are_not_echoed(self):
        for value in ({"error": {"code": "unknown", "message": TOKEN}}, {"items": None}, {"items": [], "secret": TOKEN}):
            client, _, _ = self.client(value)
            result = lookup(READ, client)
            self.assertEqual(result["cards"][0]["error"]["code"], "INVALID_RELAY_RESPONSE")
            self.assertNotIn(TOKEN, json.dumps(result))

    def test_relay_timeout_waits_for_upstream_retry(self):
        import http.client
        for cls, parent in ((_RelayHTTPConnection, http.client.HTTPConnection), (_RelayHTTPSConnection, http.client.HTTPSConnection)):
            instance = cls("example.invalid", timeout=2)
            instance.sock = Mock()
            with patch.object(parent, "connect"):
                instance.connect()
            instance.sock.settimeout.assert_called_with(30)
            self.assertEqual(instance.timeout, 2)

    def test_cli_incomplete_relay_settings_do_not_fall_back_to_direct_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / "in.json", Path(temporary) / "out.json"
            source.write_text(json.dumps(READ), encoding="utf-8")
            env = {"PUBLIC_DATA_SERVICE_KEY": "fixture-government-secret", "PUBLIC_DATA_BASE_URL": "https://apis.data.go.kr",
                   "PUBLIC_DATA_RELAY_URL": "https://relay.example.invalid", "PUBLIC_DATA_RELAY_TOKEN": ""}
            with patch.dict(os.environ, env, clear=True):
                result = lookup_main(["--in", str(source), "--out", str(target)])
            self.assertEqual(result["cards"][0]["error"]["code"], "NO_RELAY_TOKEN")
            self.assertEqual(result, read_json(target))


class RelayServerTests(OfflineCase):
    def handler(self, payload=None, headers=None, raw=None, path="/v1/query"):
        handler = object.__new__(RelayHandler)
        handler.path = path
        handler.server = SimpleNamespace(token=TOKEN, client=Mock())
        handler.server.client.get.return_value = [ROW]
        handler.headers = Message()
        body = json.dumps(payload if payload is not None else {"operation": "permit", "itemName": "가상연동정200밀리그램"}).encode() if raw is None else raw
        default = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json", "Content-Length": str(len(body))}
        if headers:
            default.update(headers)
        for name, value in default.items():
            if value is not None:
                handler.headers[name] = value
        handler.rfile = io.BytesIO(body)
        handler._send = Mock()
        return handler

    def test_permit_and_explain_allow_only_fixed_upstream_paths_and_fields(self):
        for payload, expected_path, params in (({"operation": "permit", "itemName": "가상", "maker": "가상제약"}, PERMIT_PATH, {"item_name": "가상", "entp_name": "가상제약", "pageNo": 1, "numOfRows": 100}),
                                               ({"operation": "explain", "itemSeq": "900000101"}, EXPLAIN_PATH, {"itemSeq": "900000101", "pageNo": 1, "numOfRows": 1})):
            handler = self.handler(payload)
            handler.do_POST()
            handler.server.client.get.assert_called_once_with(expected_path, params)
            self.assertEqual(handler._send.call_args.args[0], 200)

    def test_response_is_allowlisted_and_missing_classification_remains_visible_to_validator(self):
        handler = self.handler()
        handler.server.client.get.return_value = [{**ROW, "serviceKey": "fixture-secret", "patientName": "synthetic private field"}, None]
        handler.do_POST()
        result = handler._send.call_args.args[1]
        self.assertNotIn("serviceKey", result["items"][0])
        self.assertNotIn("patientName", result["items"][0])
        self.assertEqual(result["items"][1], {})
        self.assertEqual(result["items"][0]["ETC_OTC_CODE"], "일반의약품")

    def test_missing_wrong_or_duplicate_auth_never_calls_upstream(self):
        for value in (None, "Bearer wrong", "Basic " + TOKEN):
            handler = self.handler(headers={"Authorization": value})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 401)
            handler.server.client.get.assert_not_called()
        handler = self.handler()
        handler.headers["Authorization"] = "Bearer " + TOKEN
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 401)

    def test_private_fields_arbitrary_hosts_paths_and_bad_identifiers_are_rejected(self):
        bad = [None, [], {}, {"operation": "permit", "itemName": "x", "url": "http://127.0.0.1"},
               {"operation": "permit", "itemName": "x", "patientName": "synthetic"},
               {"operation": "permit", "itemName": "x", "serviceKey": "fixture"},
               {"operation": "permit", "itemName": " "}, {"operation": "permit", "itemName": "x" * 201},
               {"operation": "permit", "itemName": "x", "maker": []},
               {"operation": "explain", "itemSeq": "../secret"}, {"operation": "explain", "itemSeq": "1" * 21},
               {"operation": "explain", "itemSeq": 1}, {"operation": "arbitrary"}]
        for payload in bad:
            handler = self.handler(raw=json.dumps(payload).encode())
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 400, payload)
            handler.server.client.get.assert_not_called()

    def test_bad_json_transfer_encoding_length_and_size_are_rejected(self):
        cases = [(b"not-json", {}, 400), (b'{"operation":NaN}', {}, 400),
                 (b"{}", {"Transfer-Encoding": "chunked"}, 400),
                 (b"{}", {"Content-Length": None}, 400), (b"{}", {"Content-Length": "-1"}, 400),
                 (b"{}", {"Content-Type": "text/plain"}, 400),
                 (b"{}", {"Content-Length": str(MAX_BODY + 1)}, 413),
                 (b"{}", {"Content-Length": "9" * 5000}, 413)]
        for body, headers, status in cases:
            handler = self.handler(raw=body, headers=headers)
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], status)
            handler.server.client.get.assert_not_called()

    def test_health_unknown_path_and_unsupported_method_never_read_state(self):
        health = self.handler(path="/health")
        health.do_GET()
        health._send.assert_called_once_with(200, {"ok": True, "service": "nunmul-rx-relay"})
        unknown = self.handler(path="/state/medication_store.json")
        unknown.do_GET()
        self.assertEqual(unknown._send.call_args.args[0], 404)
        for method in ("do_GET", "do_DELETE", "do_OPTIONS", "do_PUT"):
            handler = self.handler()
            getattr(handler, method)()
            self.assertEqual(handler._send.call_args.args[0], 405)
            handler.server.client.get.assert_not_called()

    def test_upstream_errors_are_sanitized_and_use_documented_status(self):
        cases = [("NO_KEY", 503, "NO_KEY"), ("NETWORK_ERROR", 502, "RELAY_UPSTREAM_NETWORK_ERROR"),
                 ("API_ERROR", 502, "RELAY_UPSTREAM_API_ERROR"), ("HTTP_403", 502, "RELAY_UPSTREAM_HTTP_ERROR"),
                 ("INVALID_RESPONSE", 502, "RELAY_UPSTREAM_INVALID_RESPONSE")]
        for code, status, result_code in cases:
            handler = self.handler()
            handler.server.client.get.side_effect = ToolError(code, "fixture-secret " + TOKEN)
            handler.do_POST()
            result = handler._send.call_args.args
            self.assertEqual(result[0], status)
            self.assertEqual(result[1]["error"]["code"], result_code)
            self.assertNotIn(TOKEN, json.dumps(result))

    def test_unexpected_error_is_safe_and_does_not_print_traceback(self):
        handler = self.handler()
        handler.server.client.get.side_effect = RuntimeError("fixture-secret " + TOKEN)
        capture = io.StringIO()
        with redirect_stdout(capture), redirect_stderr(capture):
            handler.do_POST()
            handler.log_message("fixture-secret " + TOKEN)
        self.assertEqual(handler._send.call_args.args[0], 500)
        self.assertEqual(capture.getvalue(), "")
        self.assertNotIn(TOKEN, json.dumps(handler._send.call_args.args))

    def test_wire_response_is_one_json_with_no_cache_or_secrets(self):
        handler = self.handler()
        handler.request_version, handler.requestline = "HTTP/1.1", "POST /v1/query HTTP/1.1"
        handler.wfile = io.BytesIO()
        RelayHandler._send(handler, 200, {"items": []})
        headers, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        self.assertIn(b"Cache-Control: no-store", headers)
        self.assertEqual(json.loads(body), {"items": []})

    def test_server_will_not_bind_public_interface_or_use_missing_token(self):
        for address, token in ((("0.0.0.0", 8765), TOKEN), (("127.0.0.1", 8765), "short")):
            with self.assertRaises(ToolError):
                RelayServer(address, token, Mock())

    def test_concurrency_limit_rejects_without_creating_unbounded_threads(self):
        import threading
        server = object.__new__(RelayServer)
        server.capacity = threading.BoundedSemaphore(1)
        server.capacity.acquire()
        server.shutdown_request = Mock()
        request = Mock()
        server.process_request(request, ("127.0.0.1", 1))
        self.assertIn(b"503", request.sendall.call_args.args[0])
        server.shutdown_request.assert_called_once_with(request)

    def test_contract_names_routes_fields_and_all_error_codes(self):
        contract = read_json(ROOT / "api/relay.openapi.json")
        self.assertEqual(set(contract["paths"]), {"/health", "/v1/query"})
        post = contract["paths"]["/v1/query"]["post"]
        self.assertEqual(post["security"], [{"bearerAuth": []}])
        self.assertEqual(set(post["responses"]), {"200", "400", "401", "404", "405", "413", "502", "503", "default"})
        self.assertEqual(set(contract["components"]["x-corthex"]["errors"]), set(ERRORS))


if __name__ == "__main__":
    unittest.main()
