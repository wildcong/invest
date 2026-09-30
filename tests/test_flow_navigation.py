import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from manual_refresh import ManualRefreshResult


class FlowNavigationTests(unittest.TestCase):
    def test_search_uses_symbols_from_the_current_cache_version(self):
        root = Path(__file__).resolve().parents[1]
        cache = json.loads((root / "data" / "scan_cache.json").read_text())
        updated = copy.deepcopy(cache)
        symbols = updated["markets"]["kospi200"]["symbols"]
        original_name = next(iter(symbols))
        symbols["새로 반영된 분석 종목"] = symbols.pop(original_name)
        with (
            patch("scanner.load_scan_cache", return_value=cache) as load,
            patch("market_data.cache_file_version", return_value=(123456789, 1)) as version,
        ):
            app = AppTest.from_file(str(root / "pages" / "flow_scanner.py"), default_timeout=20).run()
            next(item for item in app.radio if item.label == "분석 시장").set_value("분석 대상 종목 검색").run()
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
