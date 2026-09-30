import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from market_data import FRED_SERIES


class USLiquidityUITests(unittest.TestCase):
    def test_four_charts_keep_values_directions_and_source_dates(self):
        cache = {
            "generated_at_utc": "2026-09-18T00:00:00+00:00",
            "series": {
                key: {
                    **metadata,
                    "rows": [
                        {"date": "2026-09-10", "value_십억달러": 100},
                        {"date": "2026-09-17", "value_십억달러": 110},
                    ],
                }
                for key, metadata in FRED_SERIES.items()
            },
        }
        page = Path(__file__).resolve().parents[1] / "pages" / "us_liquidity.py"
        with (
            patch("market_data.load_us_liquidity_cache", return_value=cache),
            patch("market_data.cache_file_version", return_value=(991, 991)),
        ):
            app = AppTest.from_file(str(page), default_timeout=20).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("plotly_chart")), 4)
        self.assertEqual([metric.value for metric in app.metric], ["$110.0B"] * 4)
        self.assertEqual(
            [metric.delta for metric in app.metric],
            ["+10.0B · 유동성 축소 방향", "+10.0B · 유동성 확대 방향"] * 2,
        )
        captions = "\n".join(item.value for item in app.caption)
        self.assertIn("최근 기준일 2026-09-17", captions)
        self.assertIn("역방향 · 하락할수록 유동성 확대 방향", captions)
        self.assertIn("정방향 · 상승할수록 유동성 확대 방향", captions)
        for metadata in FRED_SERIES.values():
            self.assertIn("FRED " + metadata["series_id"], captions)
        self.assertEqual(app.expander[0].label, "지표 읽는 법")
        self.assertFalse(app.expander[0].proto.expanded)


if __name__ == "__main__":
    unittest.main()
