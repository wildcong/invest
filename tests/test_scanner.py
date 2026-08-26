import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests

import scanner


class AccessTokenTests(unittest.TestCase):
    def test_token_endpoint_is_called_exactly_once(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"access_token": "daily-token"}
        request_post = Mock(return_value=response)

        token = scanner.get_access_token(
            "app-key",
            "app-secret",
            request_post=request_post,
        )

        self.assertEqual(token, "daily-token")
        request_post.assert_called_once()
        self.assertTrue(request_post.call_args.args[0].endswith("/oauth2/tokenP"))

    def test_token_failure_is_not_retried(self):
        request_post = Mock(side_effect=requests.ConnectionError("offline"))

        token = scanner.get_access_token(
            "app-key",
            "app-secret",
            request_post=request_post,
        )

        self.assertIsNone(token)
        request_post.assert_called_once()


class ScanCacheTests(unittest.TestCase):
    @patch("scanner.scan_market")
    @patch("scanner.get_stock_lists")
    def test_build_scan_cache_reuses_supplied_token(self, stock_lists, scan_market):
        stock_lists.return_value = (
            {"삼성전자": "005930"},
            {"에코프로": "086520"},
            {"삼성전자": "005930", "에코프로": "086520"},
        )
        scan_market.return_value = (
            {},
            {"buy": 0, "mixed": 0, "sell": 0, "scanned": 0},
            {"buy": [], "mixed": [], "sell": []},
            {},
        )

        scanner.build_scan_cache("key", "secret", "one-token")

        self.assertEqual(scan_market.call_count, 2)
        for call in scan_market.call_args_list:
            self.assertEqual(call.args[1], "one-token")

    def test_save_scan_cache_round_trip(self):
        payload = {"target_date": "20260826", "한글": [1, 2, 3]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.json"
            scanner.save_scan_cache(payload, path)
            self.assertEqual(scanner.load_scan_cache(path), payload)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_classify_five_day_direction(self):
        index = pd.date_range("2026-08-20", periods=5)
        frame = pd.DataFrame({"F_억": [1] * 5, "I_억": [2] * 5}, index=index)
        self.assertEqual(scanner.classify_5day_direction(frame), "buy")


if __name__ == "__main__":
    unittest.main()
