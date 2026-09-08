import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests

import prefetch_scan_cache as batch
import scanner
from trading_calendar import KST, completed_session, is_session_date
from test_daily_batch import NOW, TARGET_DATE, scan_cache, chart_rows


class CalendarTests(unittest.TestCase):
    def test_holiday_weekend_and_close_boundary(self):
        cases = {
            "2026-01-01T16:00": "20251230",
            "2026-09-06T18:00": "20260904",
            "2026-09-08T15:44": "20260907",
            "2026-09-08T15:45": "20260908",
            "2026-11-19T16:44": "20261118",
            "2026-11-19T16:45": "20261119",
            "2027-01-01T17:00": "20261230",
            "2027-01-04T15:47": "20261230",
            "2027-01-04T16:45": "20270104",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(completed_session(datetime.fromisoformat(text).replace(tzinfo=KST)), expected)
        self.assertFalse(is_session_date("20260101"))

    def test_scheduled_holiday_does_not_request_token(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(batch._validate_run_time(datetime(2026, 1, 1, 17, tzinfo=KST)))


class FreshnessTests(unittest.TestCase):
    def test_invalid_dates_and_nonfinite_values_cannot_count_as_current(self):
        good = chart_rows()
        cases = {}
        duplicate = copy.deepcopy(good)
        duplicate[1]["Date"] = duplicate[0]["Date"]
        cases["duplicate"] = duplicate
        cases["reversed"] = list(reversed(good))
        future = copy.deepcopy(good)
        future[-1]["Date"] = "2026-08-31"
        cases["future"] = future
        holiday = copy.deepcopy(good)
        holiday[0]["Date"] = "2026-08-23"
        cases["non-session"] = holiday
        for field, value in (("Price", 0), ("Price", -1), ("F_억", float("inf")),
                             ("I_억", float("nan")), ("P_억", float("-inf"))):
            rows = copy.deepcopy(good)
            rows[-1][field] = value
            cases[f"{field}={value}"] = rows
        missing = copy.deepcopy(good)
        missing[-1].pop("P_억")
        cases["missing"] = missing
        for label, rows in cases.items():
            with self.subTest(case=label):
                payload = scan_cache()
                payload["markets"]["kosdaq150"]["chart_data"]["000000"] = rows
                coverage = scanner.scan_coverage(payload["markets"]["kosdaq150"], TARGET_DATE, 150)
                self.assertEqual(coverage["current"], 149)
                self.assertEqual(coverage["invalid"], ["Stock0"])
                self.assertFalse(scanner.cache_has_target_date(payload, TARGET_DATE))

    def test_error_response_and_infinity_are_not_investor_observations(self):
        for error_code, value in (("1", "10"), ("0", "inf")):
            response = Mock(status_code=200)
            response.json.return_value = {"rt_cd": error_code, "output2": [{
                "stck_bsop_date": "20260828", "stck_clpr": "100",
                "frgn_ntby_tr_pbmn": value, "orgn_ntby_tr_pbmn": "20",
                "prsn_ntby_tr_pbmn": "-30",
            }]}
            with patch.object(requests, "get", return_value=response):
                self.assertTrue(scanner.get_investor_data("000001", "test", "test", "test", TARGET_DATE).empty)

    def test_serialization_rejects_missing_flow_column(self):
        frame = pd.DataFrame({"Price": [100], "F_억": [1], "I_억": [2]}, index=pd.to_datetime(["2026-08-28"]))
        with self.assertRaisesRegex(ValueError, "필드 누락"):
            scanner.serialize_chart_data(frame)

    def test_missing_flow_field_is_not_fabricated_as_zero(self):
        response = Mock(status_code=200)
        response.json.return_value = {"rt_cd": "0", "output2": [{
            "stck_bsop_date": "20260828", "stck_clpr": "100",
            "frgn_ntby_tr_pbmn": "10", "orgn_ntby_tr_pbmn": "20",
        }]}
        with patch.object(requests, "get", return_value=response):
            frame = scanner.get_investor_data("000001", "test", "test", "test", TARGET_DATE)
        self.assertTrue(frame.empty)

    def test_data_request_is_pinned_to_completed_session(self):
        response = Mock(status_code=200)
        response.json.return_value = {"rt_cd": "0", "output2": [{
            "stck_bsop_date": date, "stck_clpr": "100",
            "frgn_ntby_tr_pbmn": "10", "orgn_ntby_tr_pbmn": "20",
            "prsn_ntby_tr_pbmn": "-30",
        } for date in ("20260827", "20260828", "20260829")]}
        with patch.object(requests, "get", return_value=response) as get:
            frame = scanner.get_investor_data("000001", "test", "test", "test", TARGET_DATE)
        self.assertEqual(get.call_args.kwargs["params"]["FID_INPUT_DATE_1"], TARGET_DATE)
        self.assertEqual(frame.index[-1].strftime("%Y%m%d"), TARGET_DATE)
        self.assertEqual(len(frame), 2)

    def test_bad_observation_is_not_silently_dropped_from_signal_window(self):
        valid = [{
            "stck_bsop_date": date, "stck_clpr": "100",
            "frgn_ntby_tr_pbmn": "10", "orgn_ntby_tr_pbmn": "20",
            "prsn_ntby_tr_pbmn": "-30",
        } for date in ("20260821", "20260824", "20260825", "20260826", "20260827", "20260828")]
        for field, value in (("stck_bsop_date", ""), ("stck_bsop_date", "20260899"),
                             ("stck_bsop_date", None), ("frgn_ntby_tr_pbmn", "NaN"),
                             ("orgn_ntby_tr_pbmn", None), ("prsn_ntby_tr_pbmn", "bad")):
            with self.subTest(field=field, value=value):
                rows = copy.deepcopy(valid)
                rows[-2][field] = value
                response = Mock(status_code=200)
                response.json.return_value = {"rt_cd": "0", "output2": rows}
                with patch.object(requests, "get", return_value=response):
                    frame = scanner.get_investor_data("000001", "test", "test", "test", TARGET_DATE)
                self.assertTrue(frame.empty)

    def test_missing_api_success_status_is_rejected(self):
        response = Mock(status_code=200)
        response.json.return_value = {"output2": [{
            "stck_bsop_date": "20260828", "stck_clpr": "100",
            "frgn_ntby_tr_pbmn": "10", "orgn_ntby_tr_pbmn": "20",
            "prsn_ntby_tr_pbmn": "-30",
        }]}
        with patch.object(requests, "get", return_value=response):
            self.assertTrue(scanner.get_investor_data("000001", "test", "test", "test", TARGET_DATE).empty)

    def test_last_actual_date_is_required_not_self_assigned_target(self):
        payload = scan_cache()
        self.assertTrue(scanner.cache_has_target_date(payload, TARGET_DATE))
        for market in payload["markets"].values():
            for ticker in market["chart_data"]:
                market["chart_data"][ticker] = [{"Date": "2026-08-27"}] * 5
        self.assertFalse(scanner.cache_has_target_date(payload, TARGET_DATE))
        self.assertFalse(scanner.cache_has_target_date(payload, TARGET_DATE, allow_partial=True))

    def test_partial_is_usable_but_never_complete(self):
        payload = scan_cache()
        payload["markets"]["kosdaq150"]["chart_data"]["000000"] = chart_rows("20260827")
        self.assertFalse(scanner.cache_has_target_date(payload, TARGET_DATE))
        self.assertTrue(scanner.cache_has_target_date(payload, TARGET_DATE, allow_partial=True))
        coverage = scanner.scan_coverage(payload["markets"]["kosdaq150"], TARGET_DATE, 150)
        self.assertEqual(coverage["current"], 149)
        self.assertEqual(coverage["stale"], ["Stock0"])

    def test_tiny_universe_is_never_publishable(self):
        payload = scan_cache()
        for market in payload["markets"].values():
            market["symbols"] = {"Stock0": "000000"}
            market["market_size"] = 1
        self.assertFalse(scanner.cache_has_target_date(payload, TARGET_DATE, allow_partial=True))

    def test_listing_failure_has_no_fake_success_fallback(self):
        with patch("scanner.get_stock_universe", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                scanner.get_stock_lists()

    def test_wrong_sized_listing_is_rejected(self):
        from stock_universe import parse_listing
        frame = pd.DataFrame({"Name": ["A"], "Code": ["000001"], "Marcap": [10], "MarketId": ["STK"]})
        with self.assertRaisesRegex(ValueError, "불완전"):
            parse_listing(frame.to_csv(index=False))

    def test_partial_retries_then_publishes_degraded_without_issuing(self):
        payload = scan_cache()
        payload["markets"]["kosdaq150"]["chart_data"].pop("000000")
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            with (
                patch.object(batch, "BATCH_STATE_FILE", state_file),
                patch.object(batch, "load_scan_cache", return_value={}),
                patch.object(batch, "_credentials", return_value=("test", "test")),
                patch.object(batch, "get_or_issue_access_token", return_value=("saved", "reused")),
                patch.object(batch, "build_scan_cache", side_effect=lambda *a, **k: copy.deepcopy(payload)) as build,
                patch.object(batch, "save_scan_cache") as save,
                patch.object(batch.time, "sleep"),
                patch.object(requests, "post", side_effect=AssertionError("network forbidden")),
            ):
                batch.run_scanner_phase(NOW)
            self.assertEqual(build.call_count, 3)
            self.assertEqual(save.call_args.args[0]["quality"], "degraded")
            state = json.loads(state_file.read_text())
            self.assertEqual(state["batch"]["stages"]["scanner"]["status"], "degraded")
            self.assertEqual(state["batch"]["status"], "partial")

    def test_all_stale_result_preserves_existing_cache(self):
        payload = scan_cache()
        for market in payload["markets"].values():
            for ticker in market["chart_data"]:
                market["chart_data"][ticker] = [{"Date": "2026-08-27"}] * 5
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(batch, "BATCH_STATE_FILE", Path(directory) / "state.json"),
                patch.object(batch, "load_scan_cache", return_value={}),
                patch.object(batch, "_credentials", return_value=("test", "test")),
                patch.object(batch, "get_or_issue_access_token", return_value=("saved", "reused")),
                patch.object(batch, "build_scan_cache", return_value=payload),
                patch.object(batch, "save_scan_cache") as save,
                patch.object(batch.time, "sleep"),
            ):
                with self.assertRaises(RuntimeError):
                    batch.run_scanner_phase(NOW)
                save.assert_not_called()
