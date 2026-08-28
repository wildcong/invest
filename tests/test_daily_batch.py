import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import prefetch_scan_cache
from kis_token_store import decrypt_access_token, encrypt_access_token
from market_data import KST

NOW = datetime(2026, 8, 28, 16, 0, tzinfo=KST)
TARGET_DATE = "20260828"
APP_SECRET = "high-entropy-test-app-secret"


def program_cache(target_date: str = TARGET_DATE) -> dict:
    formatted = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    return {
        "markets": {
            "kospi": {"rows": [{"date": formatted}]},
            "kosdaq": {"rows": [{"date": formatted}]},
        }
    }


def scan_cache(target_date: str = TARGET_DATE) -> dict:
    market = {
        "target_date": target_date,
        "market_size": 1,
        "summary": {"scanned": 1},
        "direction_groups": {"mixed": [{"ticker": "000000"}]},
        "chart_data": {"000000": [{"Date": "2026-08-28"}]},
    }
    return {
        "target_date": target_date,
        "markets": {"kospi200": dict(market), "kosdaq150": dict(market)},
    }


class TokenEncryptionTests(unittest.TestCase):
    def test_encrypted_token_round_trip_and_wrong_secret_rejection(self):
        ciphertext = encrypt_access_token("short-lived-token", APP_SECRET)

        self.assertNotIn("short-lived-token", ciphertext)
        self.assertEqual(
            decrypt_access_token(ciphertext, APP_SECRET),
            "short-lived-token",
        )
        self.assertIsNone(decrypt_access_token(ciphertext, "wrong-secret"))


class TokenPersistenceTests(unittest.TestCase):
    def test_valid_encrypted_token_is_reused_without_issuance(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = {
                "version": 2,
                "token": {
                    "ciphertext": encrypt_access_token("saved-token", APP_SECRET),
                    "expires_at_kst": (NOW + timedelta(hours=3)).isoformat(),
                    "key_fingerprint": prefetch_scan_cache._secret_fingerprint(
                        APP_SECRET
                    ),
                },
            }
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(prefetch_scan_cache, "issue_access_token") as issue,
            ):
                token, source = prefetch_scan_cache.get_or_issue_access_token(
                    state,
                    "app-key",
                    APP_SECRET,
                    NOW,
                )

            self.assertEqual((token, source), ("saved-token", "reused"))
            issue.assert_not_called()

    def test_new_token_is_saved_encrypted_with_official_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            expiry = "2026-08-29 16:00:00"
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(
                    prefetch_scan_cache,
                    "issue_access_token",
                    return_value={
                        "access_token": "new-secret-token",
                        "access_token_token_expired": expiry,
                    },
                ) as issue,
            ):
                token, source = prefetch_scan_cache.get_or_issue_access_token(
                    {},
                    "app-key",
                    APP_SECRET,
                    NOW,
                )

            self.assertEqual((token, source), ("new-secret-token", "issued"))
            issue.assert_called_once()
            raw_state = state_path.read_text(encoding="utf-8")
            self.assertNotIn("new-secret-token", raw_state)
            saved = json.loads(raw_state)
            self.assertEqual(saved["token_request"]["status"], "success")
            self.assertEqual(
                decrypt_access_token(saved["token"]["ciphertext"], APP_SECRET),
                "new-secret-token",
            )
            self.assertEqual(
                saved["token"]["expires_at_kst"],
                "2026-08-29T16:00:00+09:00",
            )

    def test_failed_request_has_cooldown_but_not_a_daily_lockout(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = {}
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(
                    prefetch_scan_cache,
                    "issue_access_token",
                    side_effect=RuntimeError("issuance rate limited"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "토큰 요청 실패"):
                    prefetch_scan_cache.get_or_issue_access_token(
                        state,
                        "app-key",
                        APP_SECRET,
                        NOW,
                    )

            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["token_request"]["status"], "failed")
            self.assertNotIn("token_request_date_kst", saved)

            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(
                    prefetch_scan_cache,
                    "issue_access_token",
                    return_value={
                        "access_token": "later-token",
                        "expires_in": 86400,
                    },
                ) as retry,
            ):
                token, source = prefetch_scan_cache.get_or_issue_access_token(
                    saved,
                    "app-key",
                    APP_SECRET,
                    NOW
                    + timedelta(
                        seconds=prefetch_scan_cache.TOKEN_REQUEST_COOLDOWN_SECONDS + 1
                    ),
                )

            self.assertEqual((token, source), ("later-token", "issued"))
            retry.assert_called_once()

    def test_connect_timeout_is_retried_without_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            response = {"access_token": "connected-token", "expires_in": 86400}
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(
                    prefetch_scan_cache,
                    "issue_access_token",
                    side_effect=[requests.ConnectTimeout("blocked route"), response],
                ) as issue,
                patch.object(prefetch_scan_cache.time, "sleep") as sleep,
            ):
                token, source = prefetch_scan_cache.get_or_issue_access_token(
                    {},
                    "app-key",
                    APP_SECRET,
                    NOW,
                )

            self.assertEqual((token, source), ("connected-token", "issued"))
            self.assertEqual(issue.call_count, 2)
            sleep.assert_called_once_with(
                prefetch_scan_cache.TOKEN_CONNECT_RETRY_DELAY_SECONDS
            )

    def test_exhausted_connect_timeouts_allow_a_new_workflow_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(
                    prefetch_scan_cache,
                    "issue_access_token",
                    side_effect=requests.ConnectTimeout("blocked route"),
                ) as issue,
                patch.object(prefetch_scan_cache.time, "sleep"),
            ):
                with self.assertRaisesRegex(RuntimeError, "토큰 요청 실패"):
                    prefetch_scan_cache.get_or_issue_access_token(
                        {},
                        "app-key",
                        APP_SECRET,
                        NOW,
                    )

            self.assertEqual(issue.call_count, 3)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["token_request"]["safe_to_retry"])
            self.assertFalse(prefetch_scan_cache._request_is_cooling_down(saved, NOW))


class RetryTests(unittest.TestCase):
    def test_retries_use_exponential_backoff(self):
        operation = Mock(side_effect=[{}, {}, {"complete": True}])
        with patch.object(prefetch_scan_cache.time, "sleep") as sleep:
            result, attempts = prefetch_scan_cache.run_with_retries(
                "test operation",
                operation,
                lambda payload: payload.get("complete", False),
                attempts=3,
                base_delay_seconds=2,
            )

        self.assertEqual(result, {"complete": True})
        self.assertEqual(attempts, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])


class RunWindowTests(unittest.TestCase):
    def test_manual_recovery_can_run_before_market_close(self):
        before_close = NOW.replace(hour=14)
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(prefetch_scan_cache._validate_run_time(before_close))
        with patch.dict(os.environ, {"ALLOW_OFF_HOURS": "true"}, clear=True):
            self.assertTrue(prefetch_scan_cache._validate_run_time(before_close))


class PhaseIsolationTests(unittest.TestCase):
    def test_priority_phase_persists_program_cache_before_scanner(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(
                    prefetch_scan_cache,
                    "build_us_liquidity_cache",
                    return_value={"ok": True},
                ),
                patch.object(prefetch_scan_cache, "save_us_liquidity_cache"),
                patch.object(prefetch_scan_cache, "load_scan_cache", return_value={}),
                patch.object(
                    prefetch_scan_cache, "load_program_trade_cache", return_value={}
                ),
                patch.object(
                    prefetch_scan_cache,
                    "issue_access_token",
                    return_value={"access_token": "one-token", "expires_in": 86400},
                ),
                patch.object(
                    prefetch_scan_cache,
                    "build_program_trade_cache",
                    return_value=program_cache(),
                ) as build_program,
                patch.object(
                    prefetch_scan_cache, "save_program_trade_cache"
                ) as save_program,
                patch.object(prefetch_scan_cache, "build_scan_cache") as build_scanner,
                patch.dict(
                    os.environ,
                    {"KIS_APP_KEY": "key", "KIS_APP_SECRET": APP_SECRET},
                    clear=False,
                ),
            ):
                prefetch_scan_cache.run_priority_phase(NOW)

            build_program.assert_called_once()
            save_program.assert_called_once_with(program_cache())
            build_scanner.assert_not_called()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state["batch"]["stages"]["program_trade"]["status"], "success"
            )
            self.assertEqual(state["batch"]["stages"]["scanner"]["status"], "pending")
            self.assertNotIn("one-token", state_path.read_text(encoding="utf-8"))

    def test_scanner_phase_reuses_persisted_token(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "token": {
                            "ciphertext": encrypt_access_token(
                                "persisted-token", APP_SECRET
                            ),
                            "expires_at_kst": (NOW + timedelta(hours=12)).isoformat(),
                            "key_fingerprint": prefetch_scan_cache._secret_fingerprint(
                                APP_SECRET
                            ),
                        },
                    }
                ),
                encoding="utf-8",
            )
            completed = scan_cache()
            with (
                patch.object(prefetch_scan_cache, "BATCH_STATE_FILE", state_path),
                patch.object(prefetch_scan_cache, "load_scan_cache", return_value={}),
                patch.object(prefetch_scan_cache, "issue_access_token") as issue,
                patch.object(
                    prefetch_scan_cache, "build_scan_cache", return_value=completed
                ) as build,
                patch.object(prefetch_scan_cache, "save_scan_cache") as save,
                patch.dict(
                    os.environ,
                    {"KIS_APP_KEY": "key", "KIS_APP_SECRET": APP_SECRET},
                    clear=False,
                ),
            ):
                prefetch_scan_cache.run_scanner_phase(NOW)

            issue.assert_not_called()
            self.assertEqual(build.call_args.args[2], "persisted-token")
            save.assert_called_once()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["batch"]["stages"]["scanner"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
