from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.error
import urllib.parse

from tools.common import ToolError, read_json
from tools.rx_lookup import (CONNECT_TIMEOUT, READ_TIMEOUT, NameClue, PublicDataClient,
                             TimedHTTPConnection, TimedHTTPSConnection, explain,
                             load_env, lookup, normalize, parse_response, strengths)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CATALOG = (FIXTURES / "permit_catalog.json").read_bytes()


def response(raw=CATALOG):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = raw
    return result


def make_client(raw=CATALOG, key="fixture-only", effects=None):
    opener = Mock()
    opener.open.return_value = response(raw)
    if effects is not None:
        opener.open.side_effect = effects
    sleeper = Mock()
    client = PublicDataClient({"PUBLIC_DATA_SERVICE_KEY": key}, opener, sleeper)
    return client, opener, sleeper


def data(name="합성정", maker=None):
    return {"readable": True, "medicines": [{"readNo": 1, "readName": name, "readMaker": maker}]}


class OfflineTest(unittest.TestCase):
    def setUp(self):
        # A missed HTTP mock fails before DNS or a TCP connection is attempted.
        guard = patch("socket.create_connection", side_effect=AssertionError("Network disabled in tests"))
        guard.start()
        self.addCleanup(guard.stop)


class CandidateTests(OfflineTest):
    def card(self, name, maker=None, raw=CATALOG):
        client, opener, _ = make_client(raw)
        card = lookup(data(name, maker), client)["cards"][0]
        return card, opener

    def test_normalization_preserves_strength_and_removes_administration(self):
        self.assertEqual(normalize(" 합성정 ６０ｍｇ /１정 "), "합성정60mg")
        self.assertEqual(NameClue("합성정60mg/1정…").clean, "합성정60mg")

    def test_complete_strength_ellipsis_and_ingredient_description(self):
        for name in ("합성정60mg…", "합성정60mg...", "합성정60mg⋯", "합성정60mg‥", "합성정60mg(성분…)"):
            with self.subTest(name=name):
                card, opener = self.card(name)
                self.assertEqual(card["status"], "OK")
                self.assertEqual(card["readName"], name)
                self.assertEqual(card["queries"], ["합성정60mg"])
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual({c["itemSeq"] for c in card["candidates"]}, {"100001", "100002", "100003", "100009"})

    def test_truncated_unit_or_dosage_form_needs_name_without_http(self):
        for name in ("합성정0.5m…", "합성서방…", "합성정5mg/…", None, ""):
            with self.subTest(name=name):
                card, opener = self.card(name)
                self.assertEqual(card["status"], "NEEDS_NAME")
                self.assertEqual(card["queries"], [])
                opener.open.assert_not_called()

    def test_number_prefix_does_not_guess_the_strength(self):
        card, _ = self.card("합성정60…")
        codes = {c["itemSeq"] for c in card["candidates"]}
        self.assertEqual(card["queries"], ["합성정60"])
        self.assertIn("100001", codes)
        self.assertIn("100004", codes)
        self.assertNotIn("100005", codes)
        self.assertNotIn("100006", codes)

    def test_product_only_ellipsis_accepts_all_strengths_of_same_product(self):
        card, _ = self.card("합성정…")
        self.assertEqual(card["queries"], ["합성정"])
        amounts = {strengths(c["itemName"])[0] for c in card["candidates"]}
        self.assertEqual(amounts, {6000, 60000, 600000})

    def test_compound_prefix_preserves_decimal_and_component_order(self):
        card, _ = self.card("듀비메트서방정0.5/10…")
        self.assertEqual(card["queries"], ["듀비메트서방정0.5/10"])
        self.assertEqual({c["itemSeq"] for c in card["candidates"]}, {"100013", "100014"})
        self.assertNotIn("100015", {c["itemSeq"] for c in card["candidates"]})

    def test_complete_compound_strength_is_exact_and_ordered(self):
        card, _ = self.card("듀비메트서방정0.5/10mg")
        self.assertEqual([c["itemSeq"] for c in card["candidates"]], ["100013"])

    def test_half_milligram_equals_five_hundred_micrograms(self):
        for name in ("미세정0.5mg", "미세정500mcg", "미세정500μg", "미세정500µg"):
            card, _ = self.card(name)
            self.assertEqual({c["itemSeq"] for c in card["candidates"]}, {"100010", "100011"})

    def test_different_strength_form_release_or_brand_is_rejected(self):
        clue = NameClue("합성정60mg")
        for candidate in ("합성정6mg", "합성정600mg", "합성캡슐60mg", "합성서방정60mg",
                          "합성장용정60mg", "다른합성정60mg"):
            with self.subTest(candidate=candidate):
                self.assertFalse(clue.matches(candidate))

    def test_concentration_is_never_broadened_to_product_name(self):
        clue = NameClue("가상액5mg/mL")
        self.assertEqual(clue.queries(), ["가상액5mg/mL", "가상액5밀리그램/mL"])
        self.assertTrue(clue.matches("가상액5밀리그램/mL"))
        self.assertFalse(clue.matches("가상액50mg/mL"))
        for name in ("가상액1%", "가상정100IU", "가상정100단위"):
            self.assertEqual(NameClue(name).queries(), [name])

    def test_cancelled_last_exact_name_before_maker(self):
        card, _ = self.card("합성정60mg", "가상제약")
        self.assertEqual(card["candidates"][0]["itemSeq"], "100002")
        self.assertEqual(card["candidates"][-1]["itemSeq"], "100009")
        self.assertTrue(card["candidates"][-1]["cancelled"])

    def test_exact_maker_breaks_an_exact_name_tie(self):
        seed = read_json(FIXTURES / "permit_catalog.json")
        rows = [deepcopy(seed["body"]["items"][1]) for _ in range(2)]
        rows[0].update(ITEM_SEQ="200001", ENTP_NAME="다른가상제약")
        rows[1].update(ITEM_SEQ="200002", ENTP_NAME="가상제약")
        raw = json.dumps({"header": {"resultCode": "00"}, "body": {"items": rows}}).encode()
        card, _ = self.card("합성정60mg", "가상제약", raw)
        self.assertEqual(card["candidates"][0]["itemSeq"], "200002")

    def test_candidate_cap_and_deduplication(self):
        template = read_json(FIXTURES / "permit_catalog.json")["body"]["items"][0]
        rows = [{**template, "ITEM_SEQ": str(200000 + i)} for i in range(8)]
        rows.insert(1, deepcopy(rows[0]))
        raw = json.dumps({"header": {"resultCode": "00"}, "body": {"items": rows}}).encode()
        card, _ = self.card("합성정60밀리그램", raw=raw)
        self.assertEqual(len(card["candidates"]), 5)
        self.assertEqual(len({c["itemSeq"] for c in card["candidates"]}), 5)

    def test_incomplete_rows_are_counted_without_losing_valid_candidates(self):
        card, _ = self.card("합성정60mg", raw=(FIXTURES / "permit_partial.json").read_bytes())
        self.assertEqual(card["status"], "PARTIAL")
        self.assertEqual(card["excludedCount"], 2)
        self.assertEqual([c["itemSeq"] for c in card["candidates"]], ["100001"])

    def test_all_incomplete_rows_are_a_failure_not_official_absence(self):
        root = read_json(FIXTURES / "permit_partial.json")
        root["response"]["body"]["items"]["item"] = root["response"]["body"]["items"]["item"][1:]
        card, opener = self.card("합성정60mg", raw=json.dumps(root).encode())
        self.assertEqual(card["status"], "FAILED")
        self.assertEqual(card["error"]["code"], "INVALID_ROWS")
        self.assertEqual(opener.open.call_count, 1)

    def test_actual_response_classification_field_and_legacy_alias_are_supported(self):
        card, _ = self.card("가상연동정200밀리그램", raw=(FIXTURES / "permit_code_field.json").read_bytes())
        self.assertEqual(card["status"], "OK")
        self.assertEqual(card["candidates"][0]["etcOtc"], "일반의약품")
        self.assertEqual(card["candidates"][0]["itemSeq"], "900000101")

    def test_conflicting_or_unknown_classification_is_not_silently_accepted(self):
        seed = read_json(FIXTURES / "permit_code_field.json")
        for fields in ({"ETC_OTC_NAME": "전문의약품"}, {"ETC_OTC_CODE": "확인불가"},
                       {"ETC_OTC_CODE": None}):
            value = deepcopy(seed)
            value["body"]["items"][0].update(fields)
            card, _ = self.card("가상연동정200밀리그램", raw=json.dumps(value).encode())
            self.assertEqual(card["status"], "FAILED")
            self.assertEqual(card["error"]["code"], "INVALID_ROWS")

    def test_each_required_official_field_is_validated(self):
        template = read_json(FIXTURES / "permit_catalog.json")["body"]["items"][0]
        for field in ("ITEM_SEQ", "ITEM_NAME", "ENTP_NAME", "ETC_OTC_NAME"):
            row = {**template, field: None}
            raw = json.dumps({"header": {"resultCode": "00"}, "body": {"items": [row]}}).encode()
            with self.subTest(field=field):
                card, _ = self.card("합성정60mg", raw=raw)
                self.assertEqual(card["status"], "FAILED")
                self.assertEqual(card["excludedCount"], 1)

    def test_no_key_fails_every_card_and_makes_zero_http_calls(self):
        client, opener, _ = make_client(key="")
        input_data = data("합성정60mg")
        input_data["medicines"].append({"readNo": 2, "readName": "합성정0.5m…", "readMaker": None})
        result = lookup(input_data, client)
        self.assertTrue(all(c["status"] == "FAILED" and c["error"]["code"] == "NO_KEY" for c in result["cards"]))
        opener.open.assert_not_called()

    def test_failed_and_not_found_remain_distinct(self):
        failed, opener = self.card("합성정60mg", raw=(FIXTURES / "api_error.json").read_bytes())
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["queries"], ["합성정60mg"])
        self.assertEqual(opener.open.call_count, 1)
        missing, opener = self.card("합성정60mg", raw=(FIXTURES / "permit_empty.json").read_bytes())
        self.assertEqual(missing["status"], "NOT_FOUND")
        self.assertEqual(missing["queries"], ["합성정60mg", "합성정60밀리그램", "합성정"])
        self.assertEqual(opener.open.call_count, 3)

    def test_query_stops_at_first_match_and_maker_is_optional(self):
        client, opener, _ = make_client(effects=[response((FIXTURES / "permit_empty.json").read_bytes()), response()])
        card = lookup(data("합성정60mg", "가상제약"), client)["cards"][0]
        self.assertEqual(len(card["queries"]), 2)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.open.call_args[0][0].full_url).query)
        self.assertEqual(query["entp_name"], ["가상제약"])
        self.assertEqual(query["numOfRows"], ["100"])
        _, opener = self.card("합성정60mg")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.open.call_args[0][0].full_url).query)
        self.assertNotIn("entp_name", query)

    def test_unreadable_input_is_not_queried(self):
        client, opener, _ = make_client()
        value = data("합성정60mg")
        value["readable"] = False
        self.assertEqual(lookup(value, client)["cards"][0]["status"], "NEEDS_NAME")
        opener.open.assert_not_called()


class TransportTests(OfflineTest):
    def test_response_envelope_and_three_items_shapes(self):
        row = read_json(FIXTURES / "permit_catalog.json")["body"]["items"][0]
        for items in ([row], {"item": [row]}, {"item": row}):
            for wrapped in (False, True):
                root = {"header": {"resultCode": "00"}, "body": {"items": items}}
                if wrapped:
                    root = {"response": root}
                self.assertEqual(parse_response(json.dumps(root).encode()), [row])

    def test_xml_key_error_is_a_safe_failure(self):
        client, opener, sleeper = make_client((FIXTURES / "key_error.xml").read_bytes())
        result = lookup(data("합성정60mg"), client)["cards"][0]
        self.assertEqual(result["error"]["code"], "INVALID_RESPONSE")
        self.assertIn("키·파라미터", result["error"]["message"])
        self.assertEqual(opener.open.call_count, 1)
        sleeper.assert_not_called()

    def test_http_429_and_5xx_retry_once_after_half_second(self):
        for code in (429, 500, 503):
            error = urllib.error.HTTPError("https://example.invalid/", code, "fixture", {}, None)
            client, opener, sleeper = make_client(effects=[error, response()])
            self.assertTrue(client.get("/fixture", {}))
            self.assertEqual(opener.open.call_count, 2)
            sleeper.assert_called_once_with(0.5)

    def test_other_http_4xx_are_not_retried(self):
        for code in (400, 401, 403, 404):
            error = urllib.error.HTTPError("https://example.invalid/", code, "fixture", {}, None)
            client, opener, sleeper = make_client(effects=[error])
            with self.assertRaises(ToolError) as caught:
                client.get("/fixture", {})
            self.assertEqual(caught.exception.code, f"HTTP_{code}")
            self.assertEqual(opener.open.call_count, 1)
            sleeper.assert_not_called()

    def test_connection_error_retries_same_query_only_once(self):
        failure = urllib.error.URLError("fixture-only")
        client, opener, sleeper = make_client(effects=[failure, failure])
        result = lookup(data("합성정60mg"), client)["cards"][0]
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["queries"], ["합성정60mg"])
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(opener.open.call_args_list[0][0][0].full_url, opener.open.call_args_list[1][0][0].full_url)
        sleeper.assert_called_once_with(0.5)

    def test_decoding_key_is_urlencoded_and_not_in_results(self):
        client, opener, _ = make_client(key="fixture+/=")
        result = lookup(data("합성정60mg"), client)
        url = opener.open.call_args[0][0].full_url
        self.assertIn("serviceKey=fixture%2B%2F%3D", url)
        self.assertNotIn("fixture+/=", json.dumps(result))

    def test_upstream_error_text_is_not_echoed(self):
        raw = json.dumps({"header": {"resultCode": "30", "resultMsg": "serviceKey=fixture-only"}}).encode()
        client, _, _ = make_client(raw)
        result = lookup(data("합성정60mg"), client)
        self.assertNotIn("fixture-only", json.dumps(result))

    def test_invalid_base_url_fails_without_http(self):
        for base in ("https://[invalid", "https://example.invalid:bad", "file:///tmp", "https://user@example.invalid"):
            client, opener, _ = make_client()
            client.base = base
            with self.subTest(base=base):
                result = lookup(data("합성정60mg"), client)["cards"][0]
                self.assertEqual(result["error"]["code"], "INVALID_BASE_URL")
                opener.open.assert_not_called()

    def test_connect_and_read_timeout_are_separate_without_network(self):
        self.assertEqual(CONNECT_TIMEOUT, 2)
        self.assertEqual(READ_TIMEOUT, 10)
        for cls, parent in ((TimedHTTPConnection, http.client.HTTPConnection),
                            (TimedHTTPSConnection, http.client.HTTPSConnection)):
            connection = cls("example.invalid", timeout=CONNECT_TIMEOUT)
            connection.sock = Mock()
            with patch.object(parent, "connect"):
                connection.connect()
            self.assertEqual(connection.timeout, 2)
            connection.sock.settimeout.assert_called_once_with(10)
        client, opener, _ = make_client()
        client.get("/fixture", {})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2)

    def test_explanation_has_source_update_date_and_only_official_fields(self):
        client, opener, _ = make_client((FIXTURES / "explanation.json").read_bytes())
        result = explain("100001", client)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["source"], "식품의약품안전처 e약은요")
        self.assertEqual(result["updateDe"], "2026-11-01")
        self.assertEqual(result["efcyQesitm"], "합성 시험용 설명입니다. 실제 의약품 안내가 아닙니다.")
        self.assertIsNone(result["useMethodQesitm"])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.open.call_args[0][0].full_url).query)
        self.assertEqual(query["itemSeq"], ["100001"])
        self.assertEqual(query["numOfRows"], ["1"])

    def test_missing_explanation_is_not_found_and_failure_stays_failed(self):
        client, _, _ = make_client((FIXTURES / "permit_empty.json").read_bytes())
        self.assertEqual(explain("100001", client)["status"], "NOT_FOUND")
        client, _, _ = make_client((FIXTURES / "api_error.json").read_bytes())
        self.assertEqual(explain("100001", client)["status"], "FAILED")

    def test_env_loading_is_read_only_allowlisted_and_does_not_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = 'PUBLIC_DATA_SERVICE_KEY="fixture-only"\nPUBLIC_DATA_BASE_URL=https://apis.data.go.kr\nOTHER_VALUE=ignored\n'
            path.write_text(original, encoding="utf-8")
            environ = {"PUBLIC_DATA_SERVICE_KEY": ""}
            load_env(directory, environ)
            self.assertEqual(environ["PUBLIC_DATA_SERVICE_KEY"], "")
            self.assertEqual(environ["PUBLIC_DATA_BASE_URL"], "https://apis.data.go.kr")
            self.assertNotIn("OTHER_VALUE", environ)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_explicit_environment_does_not_open_env_file(self):
        with patch("pathlib.Path.open", side_effect=AssertionError("Must not read a real .env")):
            load_env(ROOT, {"PUBLIC_DATA_SERVICE_KEY": "", "PUBLIC_DATA_BASE_URL": "https://apis.data.go.kr",
                            "PUBLIC_DATA_RELAY_URL": "", "PUBLIC_DATA_RELAY_TOKEN": ""})

    def test_no_key_cli_is_offline_and_emits_one_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "input.json", root / "output.json"
            source.write_text(json.dumps(data("합성정60mg")), encoding="utf-8")
            environ = os.environ.copy()
            environ.update(PUBLIC_DATA_SERVICE_KEY="", PUBLIC_DATA_BASE_URL="https://apis.data.go.kr",
                           PUBLIC_DATA_RELAY_URL="", PUBLIC_DATA_RELAY_TOKEN="")
            process = subprocess.run([sys.executable, str(ROOT / "tools/rx_lookup.py"),
                                      "--in", str(source), "--out", str(target)], env=environ,
                                     capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertEqual(len(process.stdout.splitlines()), 1)
            self.assertEqual(json.loads(process.stdout), read_json(target))
            self.assertEqual(read_json(target)["cards"][0]["error"]["code"], "NO_KEY")
            self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
