import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

import scanner
from stock_universe import get_stock_universe, parse_listing
from test_daily_batch import scan_cache


def csv_listing():
    return pd.DataFrame([
        {"Code": f"{i:06d}", "Name": f"Stock{i}", "Marcap": 100000 - i,
         "MarketId": "STK" if i < 220 else "KSQ"}
        for i in range(390)
    ])


def response(text=None):
    return Mock(text=text if text is not None else csv_listing().to_csv(index=False))


class ListingProviderTests(unittest.TestCase):
    def test_missing_today_uses_valid_previous_session_with_real_as_of(self):
        get = Mock(side_effect=[requests.HTTPError("404"), response()])
        universe = get_stock_universe("20260908", request_get=get)
        self.assertEqual((len(universe.kospi), len(universe.kosdaq)), (200, 150))
        self.assertEqual(universe.metadata["as_of"], "20260907")
        self.assertEqual(universe.metadata["age_sessions"], 1)
        self.assertEqual(list(universe.kospi.values())[0], "000000")
        self.assertTrue(get.call_args_list[0].args[0].endswith("2026-09-08.csv"))
        self.assertTrue(get.call_args_list[1].args[0].endswith("2026-09-07.csv"))

    def test_every_new_collection_retries_fresh_provider_first(self):
        first = get_stock_universe("20260908", request_get=Mock(side_effect=[requests.HTTPError("404"), response()]))
        get = Mock(return_value=response())
        second = get_stock_universe("20260908", request_get=get)
        self.assertEqual(first.metadata["age_sessions"], 1)
        self.assertEqual(second.metadata["age_sessions"], 0)
        get.assert_called_once()

    def test_exhaustion_is_bounded_to_five_sessions_not_calendar_days(self):
        get = Mock(side_effect=requests.HTTPError("404"))
        with self.assertRaisesRegex(RuntimeError, "최근 5거래일"):
            get_stock_universe("20260908", request_get=get)
        self.assertEqual([call.args[0].rsplit("/", 1)[-1] for call in get.call_args_list],
                         ["2026-09-08.csv", "2026-09-07.csv", "2026-09-04.csv", "2026-09-03.csv", "2026-09-02.csv"])

    def test_bad_schema_invalid_amount_duplicate_and_small_universe_rejected(self):
        good = csv_listing()
        cases = {"schema": good.drop(columns="Marcap"), "small": good.head(2)}
        for column, value in (("Marcap", float("inf")), ("Marcap", 0), ("Code", "bad"), ("Name", ""), ("MarketId", "INVALID")):
            bad = good.astype(object)
            bad.loc[0, column] = value
            cases[f"{column}={value}"] = bad
        duplicate = good.copy()
        duplicate.loc[1, "Code"] = duplicate.loc[0, "Code"]
        cases["duplicate"] = duplicate
        for label, frame in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                parse_listing(frame.to_csv(index=False))

    def test_malformed_today_can_recover_only_from_valid_prior_listing(self):
        get = Mock(side_effect=[response("Code,Name\n000001,A\n"), response()])
        self.assertEqual(get_stock_universe("20260908", request_get=get).metadata["age_sessions"], 1)

    def test_three_map_callers_remain_compatible(self):
        universe = get_stock_universe("20260908", request_get=Mock(return_value=response()))
        with patch.object(scanner, "get_stock_universe", return_value=universe):
            result = scanner.get_stock_lists()
        self.assertEqual(tuple(map(len, result)), (200, 150, 390))

    def test_old_listing_never_suppresses_next_attempt_as_complete(self):
        payload = scan_cache()
        payload["universe"] = {"as_of": "20260827", "age_sessions": 1}
        self.assertFalse(scanner.cache_has_target_date(payload, "20260828"))
        self.assertTrue(scanner.cache_has_target_date(payload, "20260828", allow_partial=True))
