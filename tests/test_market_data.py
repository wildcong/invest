import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from market_data import (
    KST,
    build_program_trade_cache,
    build_us_liquidity_cache,
    cache_file_version,
    classify_liquidity_effect,
    normalize_program_trade_rows,
    program_cache_has_target_date,
)


class CacheFileVersionTests(unittest.TestCase):
    def test_cache_key_changes_when_deployed_file_changes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            self.assertEqual(cache_file_version(path), (0, 0))

            path.write_text("{}", encoding="utf-8")
            first_version = cache_file_version(path)
            path.write_text('{"updated":true}', encoding="utf-8")

            self.assertNotEqual(cache_file_version(path), first_version)


class FakeResponse:
    def __init__(self, *, payload=None, text=""):
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class ProgramTradeTests(unittest.TestCase):
    def test_normalize_program_trade_rows_and_derive_total(self):
        rows = normalize_program_trade_rows(
            [
                {
                    "stck_bsop_date": "20260826",
                    "nabt_smtn_ntby_tr_pbmn": "12,300",
                    "arbt_smtn_ntby_tr_pbmn": "-300",
                }
            ]
        )
        self.assertEqual(rows[0]["date"], "2026-08-26")
        self.assertEqual(rows[0]["non_arbitrage_net_억원"], 123)
        self.assertEqual(rows[0]["arbitrage_net_억원"], -3)
        self.assertEqual(rows[0]["total_program_net_억원"], 120)

    def test_program_cache_uses_same_token_for_both_markets(self):
        request_get = Mock(
            return_value=FakeResponse(
                payload={
                    "rt_cd": "0",
                    "output": [
                        {
                            "stck_bsop_date": "20260826",
                            "nabt_smtn_ntby_tr_pbmn": "100",
                            "arbt_smtn_ntby_tr_pbmn": "20",
                        }
                    ],
                }
            )
        )
        cache = build_program_trade_cache(
            "one-token",
            "key",
            "secret",
            now=datetime(2026, 8, 26, 16, tzinfo=KST),
            request_get=request_get,
        )

        self.assertEqual(request_get.call_count, 2)
        for call in request_get.call_args_list:
            self.assertEqual(call.kwargs["headers"]["authorization"], "Bearer one-token")
        self.assertTrue(program_cache_has_target_date(cache, "20260826"))


class FredTests(unittest.TestCase):
    def test_liquidity_direction_labels(self):
        self.assertEqual(
            classify_liquidity_effect(10, "direct"),
            "유동성 확대 방향",
        )
        self.assertEqual(
            classify_liquidity_effect(10, "inverse"),
            "유동성 축소 방향",
        )
        self.assertEqual(
            classify_liquidity_effect(-10, "inverse"),
            "유동성 확대 방향",
        )

    def test_fred_series_are_scaled_to_billions(self):
        values = {
            "WDTGAL": "DATE,WDTGAL\n2026-08-19,936406\n",
            "M2SL": "DATE,M2SL\n2026-07-01,23218\n",
            "RRPONTSYD": "DATE,RRPONTSYD\n2026-08-25,0.405\n",
            "WRESBAL": "DATE,WRESBAL\n2026-08-19,2935287\n",
        }

        def fake_get(_url, *, params, timeout):
            self.assertEqual(timeout, 30)
            return FakeResponse(text=values[params["id"]])

        cache = build_us_liquidity_cache(
            now=datetime(2026, 8, 26, tzinfo=KST),
            request_get=fake_get,
        )

        self.assertAlmostEqual(cache["series"]["tga"]["rows"][0]["value_십억달러"], 936.406)
        self.assertEqual(cache["series"]["m2"]["rows"][0]["value_십억달러"], 23218)
        self.assertAlmostEqual(
            cache["series"]["reserve_balances"]["rows"][0]["value_십억달러"],
            2935.287,
        )
        self.assertEqual(cache["series"]["tga"]["liquidity_relation"], "inverse")
        self.assertEqual(cache["series"]["m2"]["liquidity_relation"], "direct")


if __name__ == "__main__":
    unittest.main()
