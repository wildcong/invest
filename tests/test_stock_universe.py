import unittest
from unittest.mock import Mock, patch

import scanner
from stock_universe import (
    MARKET_CAP_PATH,
    MARKET_CAP_TR_ID,
    STOCK_UNIVERSE_SOURCE,
    get_stock_universe,
)
from test_daily_batch import scan_cache


def market_rows(prefix: str, start: int, count: int, code_offset: int) -> list[dict]:
    return [
        {
            "mksc_shrn_iscd": f"{code_offset + index:06d}",
            "data_rank": str(index),
            "hts_kor_isnm": f"{prefix}{index}",
            "stck_avls": str(1_000_000 - index),
        }
        for index in range(start, start + count)
    ]


def response(rows: object, continuation: str = "") -> Mock:
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = {"rt_cd": "0", "output": rows}
    result.headers = {"tr_cont": continuation}
    return result


def complete_responses() -> list[Mock]:
    return [
        response(market_rows("Kospi", 1, 120, 0), "M"),
        response(market_rows("Kospi", 121, 80, 0)),
        response(market_rows("Kosdaq", 1, 100, 300_000), "M"),
        response(market_rows("Kosdaq", 101, 50, 300_000)),
    ]


class KisUniverseTests(unittest.TestCase):
    def test_collects_exact_market_sizes_with_official_continuation(self):
        get = Mock(side_effect=complete_responses())
        sleep = Mock()

        universe = get_stock_universe(
            "token",
            "app-key",
            "app-secret",
            "20260908",
            request_get=get,
            sleep=sleep,
        )

        self.assertEqual((len(universe.kospi), len(universe.kosdaq)), (200, 150))
        self.assertEqual(len(universe.all_symbols), 350)
        self.assertEqual(universe.metadata["as_of"], "20260908")
        self.assertEqual(universe.metadata["source"], STOCK_UNIVERSE_SOURCE)
        self.assertEqual(universe.metadata["pages"], {"kospi": 2, "kosdaq": 2})
        self.assertEqual(get.call_count, 4)
        self.assertEqual(
            [call.kwargs["headers"]["tr_cont"] for call in get.call_args_list],
            ["", "N", "", "N"],
        )
        self.assertEqual(
            [call.kwargs["params"]["fid_input_iscd"] for call in get.call_args_list],
            ["0001", "0001", "1001", "1001"],
        )
        for call in get.call_args_list:
            self.assertTrue(call.args[0].endswith(MARKET_CAP_PATH))
            self.assertEqual(call.kwargs["headers"]["tr_id"], MARKET_CAP_TR_ID)
            self.assertEqual(call.kwargs["headers"]["authorization"], "Bearer token")
        self.assertEqual(sleep.call_count, 2)

    def test_incomplete_response_is_rejected_without_a_fallback_universe(self):
        get = Mock(return_value=response(market_rows("Kospi", 1, 30, 0)))
        with self.assertRaisesRegex(RuntimeError, "불완전"):
            get_stock_universe(
                "token", "key", "secret", "20260908", request_get=get
            )
        get.assert_called_once()

    def test_repeated_continuation_page_is_rejected(self):
        page = response(market_rows("Kospi", 1, 30, 0), "M")
        with self.assertRaisesRegex(RuntimeError, "같은 페이지"):
            get_stock_universe(
                "token",
                "key",
                "secret",
                "20260908",
                request_get=Mock(side_effect=[page, page]),
                sleep=Mock(),
            )

    def test_market_cap_order_must_be_descending(self):
        rows = market_rows("Kospi", 1, 200, 0)
        rows[1]["stck_avls"] = str(int(rows[0]["stck_avls"]) + 1)
        with self.assertRaisesRegex(ValueError, "순서"):
            get_stock_universe(
                "token",
                "key",
                "secret",
                "20260908",
                request_get=Mock(return_value=response(rows)),
            )

    def test_missing_or_invalid_required_values_are_rejected(self):
        valid = market_rows("Kospi", 1, 30, 0)
        cases = {
            "empty": [],
            "missing code": [{**valid[0], "mksc_shrn_iscd": ""}],
            "bad rank": [{**valid[0], "data_rank": "none"}],
            "bad market cap": [{**valid[0], "stck_avls": "0"}],
            "missing name": [{**valid[0], "hts_kor_isnm": ""}],
        }
        for label, rows in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                get_stock_universe(
                    "token",
                    "key",
                    "secret",
                    "20260908",
                    request_get=Mock(return_value=response(rows)),
                )

    def test_search_lists_come_from_the_kis_scan_cache(self):
        payload = scan_cache()
        with patch.object(scanner, "load_scan_cache", return_value=payload):
            kospi, kosdaq, all_symbols = scanner.get_stock_lists()
        self.assertEqual((len(kospi), len(kosdaq), len(all_symbols)), (200, 150, 200))
        self.assertEqual(kospi, payload["markets"]["kospi200"]["symbols"])

    def test_old_universe_date_never_counts_as_complete(self):
        payload = scan_cache()
        payload["universe"] = {"as_of": "20260827", "age_sessions": 1}
        self.assertFalse(scanner.cache_has_target_date(payload, "20260828"))
        self.assertFalse(scanner.cache_has_target_date(payload, "20260828", allow_partial=True))


if __name__ == "__main__":
    unittest.main()
