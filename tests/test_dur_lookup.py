from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

from tools.common import ToolError, parse_instant, read_json
from tools.dur_lookup import check, collect, current_conditions
from tools.dur_rules import DUR_PATHS, validate_page
from tools.rx_lookup import PublicDataClient, parse_response
from tools.rx_relay import RelayClient, query_upstream


ROOT = Path(__file__).resolve().parents[1]
CATALOG = read_json(ROOT / "fixtures/dur_catalog.json")
NOW = parse_instant("2026-11-01T15:00:00+09:00")
ITEMS = [{"itemSeq": "900001", "name": "가상A정"}, {"itemSeq": "900002", "name": "가상B정"}]


def clinical(**changes):
    return {"confirmedOn": "2026-11-01", "ageYears": 30, "pregnant": False, **changes}


def page(rows, number=1, total=None):
    return {"items": rows, "totalCount": len(rows) if total is None else total, "pageNo": number, "numOfRows": 100}


class DurTests(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("Network disabled in unit tests"))
        guard.start()
        self.addCleanup(guard.stop)

    def client(self, catalog=None):
        catalog = CATALOG if catalog is None else catalog
        client = Mock()
        def get(endpoint, params):
            kind = next(kind for kind, path in DUR_PATHS.items() if path == endpoint)
            rows = [deepcopy(row) for row in catalog.get(kind, []) if row["ITEM_SEQ"] == params["itemSeq"]]
            offset = (params["pageNo"] - 1) * 100
            return page(rows[offset:offset + 100], params["pageNo"], len(rows))
        client.get_page.side_effect = get
        return client

    def test_pair_is_evaluated_by_exact_product_code_and_symmetric_warnings_are_deduplicated(self):
        client = self.client()
        result = check(ITEMS, clinical(), client, NOW, "profile_mom", "엄마")
        self.assertEqual(result["lookup"], "ALL")
        self.assertEqual(result["result"], "FOUND")
        pairs = [w for w in result["warnings"] if w["type"] == "INTERACTION"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["itemSeqs"], ["900001", "900002"])
        self.assertEqual(result["profileId"], "profile_mom")
        self.assertEqual(result["expectedCount"], 4)
        self.assertTrue(all("source" in source and "checkedAt" in source for source in result["sources"]))
        self.assertIn("보장하지", result["notice"])

    def test_unselected_partner_is_not_a_match(self):
        result = check([ITEMS[0], {"itemSeq": "900003", "name": "가상C정"}], clinical(), self.client(), NOW)
        self.assertEqual(result["result"], "NONE")
        self.assertEqual(result["warnings"], [])

    def test_four_types_fresh_age_and_pregnancy_conditions(self):
        result = check(ITEMS, clinical(ageYears=70, pregnant=True), self.client(), NOW)
        self.assertEqual(set(result["checkedTypes"]), {"AGE", "INTERACTION", "ELDERLY", "PREGNANCY"})
        self.assertEqual(result["checkedCount"], 8)
        self.assertEqual({w["type"] for w in result["warnings"]}, {"INTERACTION", "ELDERLY", "PREGNANCY"})

    def test_unknown_or_stale_clinical_conditions_are_never_assumed_false(self):
        for conditions in ({}, clinical(confirmedOn="2026-10-31")):
            result = check([ITEMS[0]], conditions, self.client(), NOW)
            self.assertEqual(result["lookup"], "ALL")
            self.assertEqual(result["result"], "UNKNOWN")
            self.assertTrue(all(w["match"] == "UNKNOWN" for w in result["warnings"]))
            self.assertIsNone(result["conditions"]["pregnant"])

    def test_age_boundaries_and_months_do_not_guess_birthdate(self):
        catalog = {"AGE": [{"ITEM_SEQ":"900001", "TYPE_NAME":"연령금기", "PROHBT_CONTENT":"18개월 미만"}]}
        for condition, expected in (({"ageMonths":17}, "FOUND"), ({"ageMonths":18}, "NONE"), ({"ageYears":1}, "UNKNOWN")):
            result = check([ITEMS[0]], {"confirmedOn":"2026-11-01", "pregnant":False, **condition}, self.client(catalog), NOW)
            self.assertEqual(result["result"], expected)
        with self.assertRaises(ToolError):
            current_conditions(clinical(ageYears=True), NOW)

    def test_complex_age_condition_remains_unknown(self):
        catalog = {"AGE": [{"ITEM_SEQ":"900001", "PROHBT_CONTENT":"12세 미만 또는 별도 조건"}]}
        self.assertEqual(check([ITEMS[0]], clinical(), self.client(catalog), NOW)["result"], "UNKNOWN")

    def test_complete_empty_response_is_none_but_not_a_safety_guarantee(self):
        result = check(ITEMS, {}, self.client({}), NOW)
        self.assertEqual(result["lookup"], "ALL")
        self.assertEqual(result["result"], "NONE")
        self.assertTrue(all(source["updatedAt"] is None for source in result["sources"]))
        self.assertIn("의사", result["notice"])

    def test_manual_unidentified_drug_makes_result_incomplete(self):
        result = check([ITEMS[0], {"itemSeq":None,"name":"직접 입력 가상약"}], clinical(), self.client({}), NOW)
        self.assertEqual(result["lookup"], "PARTIAL")
        self.assertEqual(result["result"], "UNKNOWN")
        self.assertEqual(result["unidentifiedDrugNames"], ["직접 입력 가상약"])

    def test_no_drugs_and_only_manual_drugs_make_zero_calls(self):
        client = self.client()
        self.assertEqual(check([], {}, client, NOW)["lookup"], "NO_DRUG")
        self.assertEqual(check([{"name":"가상","itemSeq":None}], {}, client, NOW)["lookup"], "FAILED")
        client.get_page.assert_not_called()

    def test_missing_key_fails_without_network(self):
        client = PublicDataClient({"PUBLIC_DATA_SERVICE_KEY":""})
        result = check(ITEMS, clinical(), client, NOW)
        self.assertEqual(result["lookup"], "FAILED")
        self.assertEqual(result["result"], "UNKNOWN")
        self.assertTrue(all(source["error"]["code"] == "NO_KEY" for source in result["sources"]))

    def test_partial_upstream_failure_is_not_converted_to_none_or_error_text(self):
        client = self.client({})
        normal = client.get_page.side_effect
        def get(endpoint, params):
            if endpoint == DUR_PATHS["AGE"] and params["itemSeq"] == "900001":
                raise ToolError("NETWORK_ERROR", "fixture-service-key must not be echoed")
            return normal(endpoint, params)
        client.get_page.side_effect = get
        result = check(ITEMS, clinical(), client, NOW)
        self.assertEqual(result["lookup"], "PARTIAL")
        self.assertEqual(result["result"], "UNKNOWN")
        self.assertNotIn("fixture-service-key", json.dumps(result))

    def test_all_pages_are_read_and_official_update_date_is_preserved(self):
        rows = [{"ITEM_SEQ":"900001", "PROHBT_CONTENT":"12세 미만", "INGR_NAME":"가상"+str(n), "CHANGE_DATE":"20261001"} for n in range(101)]
        client = self.client({"AGE":rows})
        evidence = collect("900001", "AGE", client, NOW)
        self.assertEqual(client.get_page.call_count, 2)
        self.assertEqual(len(evidence["rows"]), 101)
        self.assertEqual(evidence["updatedAt"], "2026-10-01")

    def test_wrong_page_changed_total_duplicate_and_missing_rows_are_failures(self):
        row = {"ITEM_SEQ":"900001", "PROHBT_CONTENT":"12세 미만"}
        cases = [page([row], number=2), page([], total=1), page([row,row], total=2),
                 page([{**row,"ITEM_SEQ":"900002"}]), page([{**row,"TYPE_NAME":"임부금기"}])]
        for value in cases:
            client = Mock()
            client.get_page.return_value = value
            with self.assertRaises(ToolError):
                collect("900001", "AGE", client, NOW)
        rows = [{**row,"INGR_NAME":str(i)} for i in range(100)]
        client = Mock()
        client.get_page.side_effect = [page(rows,total=101),page([row],number=2,total=102)]
        with self.assertRaises(ToolError):
            collect("900001", "AGE", client, NOW)

    def test_second_page_failure_discards_first_page_as_complete_evidence(self):
        rows = [{"ITEM_SEQ":"900001", "PROHBT_CONTENT":"12세 미만", "INGR_NAME":str(i)} for i in range(100)]
        client = Mock()
        client.get_page.side_effect = [page(rows,total=101), ToolError("NETWORK_ERROR","fixture")]
        result = check([ITEMS[0]], clinical(), client, NOW)
        self.assertEqual(result["lookup"], "FAILED")
        self.assertEqual(result["warnings"], [])

    def test_excessive_total_or_invalid_interaction_partner_never_returns_none(self):
        client = Mock()
        client.get_page.return_value = page([{}]*100,total=10001)
        with self.assertRaises(ToolError) as caught:
            collect("900001", "AGE", client, NOW)
        self.assertEqual(caught.exception.code,"DUR_LIMIT")
        client.get_page.return_value = page([{"ITEM_SEQ":"900001","PROHBT_CONTENT":"합성","MIXTURE_ITEM_SEQ":None}])
        with self.assertRaises(ToolError):
            collect("900001", "INTERACTION", client, NOW)

    def test_empty_interaction_row_is_supported_without_inventing_warning(self):
        client = Mock()
        client.get_page.return_value = page([{"ITEM_SEQ":"900001","MIXTURE_ITEM_SEQ":None,"PROHBT_CONTENT":None}])
        result = collect("900001", "INTERACTION", client, NOW)
        self.assertEqual(result["rows"][0]["content"], "")

    def test_direct_response_pagination_normalizes_all_supported_item_shapes(self):
        row = {"ITEM_SEQ":"900001", "PROHBT_CONTENT":"합성"}
        for items in ([row], {"item":[row]}, {"item":row}):
            raw = json.dumps({"response":{"header":{"resultCode":"00"},"body":{"items":items,"totalCount":"1","pageNo":"1","numOfRows":"100"}}}).encode()
            normalized = validate_page(parse_response(raw, include_pagination=True),1)
            self.assertEqual(normalized["items"],[row])
            self.assertEqual(parse_response(raw),[row])

    def test_relay_dur_transport_is_allowlisted_and_does_not_send_clinical_fields(self):
        opener = Mock()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(page([])).encode()
        opener.open.return_value = response
        client = RelayClient({"PUBLIC_DATA_RELAY_URL":"https://fixture.example.invalid","PUBLIC_DATA_RELAY_TOKEN":"fixture-relay-token-0123456789012345678901"},opener)
        result = client.get_page(DUR_PATHS["AGE"], {"itemSeq":"900001","pageNo":1,"numOfRows":100,"ageYears":70,"pregnant":True})
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload,{"operation":"dur","type":"AGE","itemSeq":"900001","pageNo":1})
        self.assertEqual(result["totalCount"],0)

    def test_relay_server_rejects_extra_clinical_fields_and_uses_fixed_page_size(self):
        client = Mock()
        client.get_page.return_value = page([])
        payload = {"operation":"dur","type":"PREGNANCY","itemSeq":"900001","pageNo":1}
        self.assertEqual(query_upstream(payload,client),page([]))
        client.get_page.assert_called_once_with(DUR_PATHS["PREGNANCY"],{"itemSeq":"900001","pageNo":1,"numOfRows":100})
        for bad in ({**payload,"pregnant":True},{**payload,"url":"http://localhost"},{**payload,"pageNo":0},{**payload,"type":[]}):
            with self.assertRaises(ToolError):
                query_upstream(bad,client)


if __name__ == "__main__":
    unittest.main()
