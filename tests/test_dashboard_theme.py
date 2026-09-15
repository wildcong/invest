import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from theme_palette import THEME_OPTIONS, cumulative_line_color


def relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast_ratio(first: str, second: str) -> float:
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class DashboardThemeTests(unittest.TestCase):
    def test_page_has_three_way_theme_picker(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(app_path), default_timeout=20).run()
        self.assertFalse(app.exception)
        picker = next(item for item in app.get("button_group") if item.key == "invest_theme_mode")
        self.assertEqual(picker.options, list(THEME_OPTIONS))
        self.assertEqual(picker.value, "시스템")

        picker.select("다크").run()
        self.assertFalse(app.exception)
        picker = next(item for item in app.get("button_group") if item.key == "invest_theme_mode")
        self.assertEqual(picker.value, "다크")

    def test_cumulative_line_uses_visible_yellow_in_each_mode(self):
        cases = (
            ("다크", "light", "#0e1117", "#facc15"),
            ("라이트", "dark", "#ffffff", "#a16207"),
            ("시스템", "dark", "#0e1117", "#facc15"),
            ("시스템", "light", "#ffffff", "#a16207"),
            ("시스템", None, "#0e1117", "#b58900"),
            ("시스템", None, "#ffffff", "#b58900"),
        )
        for mode, system_theme, background, expected in cases:
            with self.subTest(mode=mode, system_theme=system_theme, background=background):
                color = cumulative_line_color(mode, system_theme)
                self.assertEqual(color, expected)
                self.assertGreaterEqual(contrast_ratio(color, background), 3)


if __name__ == "__main__":
    unittest.main()
