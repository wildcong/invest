import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from manual_refresh import ManualRefreshResult


class FlowNavigationTests(unittest.TestCase):
    def test_manual_button_runs_direct_kis_refresher(self):
        page = Path(__file__).resolve().parents[1] / "pages" / "flow_scanner.py"
        result = ManualRefreshResult(
            target_date="20260911",
            status="success",
            message="수동 갱신이 완료됐습니다.",
        )
        with patch(
            "manual_refresh.run_direct_scan_refresh",
            return_value=result,
        ) as refresh:
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
