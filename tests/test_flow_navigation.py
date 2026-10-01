import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from manual_refresh import ManualRefreshResult


class FlowNavigationTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()

    def test_search_includes_kis_stocks_outside_the_daily_350(self):
        root = Path(__file__).resolve().parents[1]
        outside_name = "상위권 밖 테스트 종목"
        outside_ticker = "999999"
        chart = pd.DataFrame(
            {"Price": [100.0] * 5, "F_억": [1.0] * 5, "I_억": [2.0] * 5, "P_억": [-3.0] * 5},
            index=pd.bdate_range(end="2026-09-30", periods=5),
        )
        with (
            patch("stock_universe.get_search_symbols", return_value={outside_name: outside_ticker}) as search,
            patch("stock_detail.load_stock_detail", return_value=chart) as detail,
        ):
            app = AppTest.from_file(str(root / "pages" / "flow_scanner.py"), default_timeout=20)
            app.secrets["KIS_APP_KEY"] = "streamlit-key"
            app.secrets["KIS_APP_SECRET"] = "streamlit-secret"
            app.run()
            next(item for item in app.radio if item.label == "분석 시장").set_value("분석 대상 종목 검색").run()
            self.assertFalse(app.exception)
            next(item for item in app.text_input if item.label == "종목명 검색").set_value(outside_name).run()
            app.run()

        self.assertFalse(app.exception)
        search.assert_called_once()
        detail.assert_called_once()
        self.assertEqual(detail.call_args.args[0], outside_ticker)
        selector = next(item for item in app.selectbox if item.label == "종목 선택")
        self.assertEqual(selector.options, [outside_name])
        self.assertTrue(any(outside_name in item.value for item in app.subheader))

    def test_search_fallback_uses_symbols_from_the_current_cache_version(self):
        root = Path(__file__).resolve().parents[1]
        cache = json.loads((root / "data" / "scan_cache.json").read_text())
        updated = copy.deepcopy(cache)
        symbols = updated["markets"]["kospi200"]["symbols"]
        original_name = next(iter(symbols))
        ticker = symbols.pop(original_name)
        symbols["새로 반영된 분석 종목"] = ticker
        with (
            patch("scanner.load_scan_cache", return_value=cache) as load,
            patch("market_data.cache_file_version", return_value=(123456789, 1)) as version,
            patch("stock_universe.get_search_symbols", side_effect=RuntimeError("KIS unavailable")),
        ):
            app = AppTest.from_file(str(root / "pages" / "flow_scanner.py"), default_timeout=20).run()
            next(item for item in app.radio if item.label == "분석 시장").set_value("분석 대상 종목 검색").run()
            next(item for item in app.text_input if item.label == "종목명 검색").set_value(ticker).run()
            self.assertFalse(app.exception)
            selector = next(item for item in app.selectbox if item.label == "종목 선택")
            self.assertIn(original_name, selector.options)

            load.return_value = updated
            version.return_value = (123456789, 2)
            app.run()
            self.assertFalse(app.exception)
            selector = next(item for item in app.selectbox if item.label == "종목 선택")
            self.assertIn("새로 반영된 분석 종목", selector.options)
            self.assertNotIn(original_name, selector.options)

    def test_manual_button_runs_direct_kis_refresher(self):
        page = Path(__file__).resolve().parents[1] / "pages" / "flow_scanner.py"
        result = ManualRefreshResult(
            target_date="20260911",
            status="success",
            message="수동 갱신이 완료됐습니다.",
        )
        with (
            patch(
                "manual_refresh.run_direct_scan_refresh",
                return_value=result,
            ) as refresh,
            patch("scanner.cache_has_target_date", return_value=False),
            patch("trading_calendar.kis_collection_ready_at", return_value=None),
        ):
            app = AppTest.from_file(str(page), default_timeout=20)
            app.secrets["KIS_APP_KEY"] = "streamlit-key"
            app.secrets["KIS_APP_SECRET"] = "streamlit-secret"
            app.run()
            self.assertFalse(app.exception)

            manual_button = next(
                item for item in app.button if "수동 갱신" in item.label
            )
            self.assertFalse(manual_button.disabled)
            manual_button.click().run()

        self.assertFalse(app.exception)
        refresh.assert_called_once_with("streamlit-key", "streamlit-secret")

    def test_previous_and_next_buttons_move_stock_selection(self):
        page = Path(__file__).resolve().parents[1] / "pages" / "flow_scanner.py"
        app = AppTest.from_file(str(page), default_timeout=20).run()
        self.assertFalse(app.exception)
        manual_buttons = [item for item in app.button if "수동 갱신" in item.label]
        self.assertEqual(len(manual_buttons), 1)

        selector = next(item for item in app.selectbox if item.label == "종목 선택")
        first_selection = selector.value
        previous = next(item for item in app.button if "이전 종목" in item.label)
        next_button = next(item for item in app.button if "다음 종목" in item.label)
        self.assertTrue(previous.disabled)

        next_button.click().run()
        self.assertFalse(app.exception)
        second_selection = next(
            item for item in app.selectbox if item.label == "종목 선택"
        ).value
        self.assertNotEqual(second_selection, first_selection)

        next(item for item in app.button if "이전 종목" in item.label).click().run()
        self.assertFalse(app.exception)
        restored_selection = next(
            item for item in app.selectbox if item.label == "종목 선택"
        ).value
        self.assertEqual(restored_selection, first_selection)


if __name__ == "__main__":
    unittest.main()
