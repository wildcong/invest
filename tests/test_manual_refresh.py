import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import manual_refresh
from market_data import KST
from test_daily_batch import NOW, TARGET_DATE, scan_cache


class ManualRefreshTests(unittest.TestCase):
    def test_completed_target_never_calls_scanner(self):
        scanner_runner = Mock()
        result = manual_refresh.run_direct_scan_refresh(
            "key",
            "secret",
            now=NOW,
            scanner_runner=scanner_runner,
            cache_loader=Mock(return_value=scan_cache()),
        )
        self.assertEqual(result.status, "already_current")
        scanner_runner.assert_not_called()

    def test_direct_refresh_sets_credentials_and_persists_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            seen_environment = {}

            def runner(now):
                seen_environment.update(
                    {
                        "key": os.environ.get("KIS_APP_KEY"),
                        "secret": os.environ.get("KIS_APP_SECRET"),
                        "off_hours": os.environ.get("ALLOW_OFF_HOURS"),
                        "now": now,
                    }
                )

            cache_loader = Mock(side_effect=[{}, {}, scan_cache()])
            result = manual_refresh.run_direct_scan_refresh(
                "manual-key",
                "manual-secret",
                now=NOW,
                state_path=state_path,
                lock_path=root / "refresh.lock",
                scanner_runner=runner,
                cache_loader=cache_loader,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(seen_environment["key"], "manual-key")
            self.assertEqual(seen_environment["secret"], "manual-secret")
            self.assertEqual(seen_environment["off_hours"], "true")
            self.assertEqual(seen_environment["now"], NOW)
            self.assertNotEqual(os.environ.get("KIS_APP_KEY"), "manual-key")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "success")
            self.assertEqual(state["target_date"], TARGET_DATE)

    def test_failure_blocks_an_immediate_duplicate_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            scanner_runner = Mock(side_effect=RuntimeError("upstream unavailable"))
            kwargs = {
                "now": NOW,
                "state_path": state_path,
                "lock_path": root / "refresh.lock",
                "scanner_runner": scanner_runner,
                "cache_loader": Mock(return_value={}),
            }
            with self.assertRaisesRegex(manual_refresh.ManualRefreshError, "수동 갱신 실패"):
                manual_refresh.run_direct_scan_refresh("key", "secret", **kwargs)

            retry_now = datetime(2026, 8, 28, 16, 1, tzinfo=KST)
            kwargs["now"] = retry_now
            with self.assertRaises(manual_refresh.ManualRefreshCooldown):
                manual_refresh.run_direct_scan_refresh("key", "secret", **kwargs)
            self.assertEqual(scanner_runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()
