import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import prefetch_scan_cache
from market_data import KST


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = cls(2026, 8, 26, 16, 0, tzinfo=KST)
        return fixed if tz else fixed.replace(tzinfo=None)


class DailyBatchGuardTests(unittest.TestCase):
    def test_same_day_state_prevents_second_token_request(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps({"token_request_date_kst": "2026-08-26"}),
                encoding="utf-8",
            )
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(prefetch_scan_cache, "datetime", FixedDateTime),
                patch.object(prefetch_scan_cache, "refresh_us_liquidity_cache"),
                patch.object(prefetch_scan_cache, "load_scan_cache", return_value={}),
                patch.object(prefetch_scan_cache, "load_program_trade_cache", return_value={}),
                patch.object(prefetch_scan_cache, "get_access_token") as token_request,
                patch.dict(
                    os.environ,
                    {"KIS_APP_KEY": "key", "KIS_APP_SECRET": "secret"},
                    clear=False,
                ),
            ):
                prefetch_scan_cache.main()

            token_request.assert_not_called()

    def test_failed_request_still_records_daily_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(prefetch_scan_cache, "datetime", FixedDateTime),
                patch.object(prefetch_scan_cache, "refresh_us_liquidity_cache"),
                patch.object(prefetch_scan_cache, "load_scan_cache", return_value={}),
                patch.object(prefetch_scan_cache, "load_program_trade_cache", return_value={}),
                patch.object(prefetch_scan_cache, "get_access_token", return_value=None) as token_request,
                patch.dict(
                    os.environ,
                    {"KIS_APP_KEY": "key", "KIS_APP_SECRET": "secret"},
                    clear=False,
                ),
            ):
                with self.assertRaises(RuntimeError):
                    prefetch_scan_cache.main()

            token_request.assert_called_once()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["token_request_date_kst"], "2026-08-26")


if __name__ == "__main__":
    unittest.main()
