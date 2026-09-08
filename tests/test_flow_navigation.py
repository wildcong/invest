import unittest
import os
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class FlowNavigationTests(unittest.TestCase):
    def test_public_page_never_uses_server_dispatch_token(self):
        page = Path(__file__).resolve().parents[1] / "pages" / "flow_scanner.py"
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS_TOKEN": "must-not-be-used"}),
            patch("github_actions.dispatch_market_cache_workflow") as dispatch,
        ):
            app = AppTest.from_file(str(page), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertFalse([item for item in app.button if "수동 갱신" in item.label])
            links = [item for item in app.get("link_button") if "수동 갱신" in item.proto.label]
            self.assertEqual(len(links), 1)
            dispatch.assert_not_called()

    def test_previous_and_next_buttons_move_stock_selection(self):
        page = Path(__file__).resolve().parents[1] / "pages" / "flow_scanner.py"
        app = AppTest.from_file(str(page), default_timeout=20).run()
        self.assertFalse(app.exception)
        manual_buttons = [item for item in app.button if "수동 갱신" in item.label]
        manual_buttons.extend(
            item
            for item in app.get("link_button")
            if "수동 갱신" in item.proto.label
        )
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
