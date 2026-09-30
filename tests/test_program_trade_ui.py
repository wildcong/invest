import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest


class ProgramTradeUiTests(unittest.TestCase):
    def test_compact_metrics_and_market_switch_keep_actual_values(self):
        def rows(value):
            return [{"date": f"2026-09-{day:02d}", "non_arbitrage_net_억원": value,
                     "arbitrage_net_억원": 2, "total_program_net_억원": value + 2}
                    for day in (14, 15, 16, 17, 18)]
        payload = {"source": "KIS test", "markets": {
            "kospi": {"rows": rows(10)}, "kosdaq": {"rows": rows(-5)},
        }}
        page = Path(__file__).resolve().parents[1] / "pages" / "program_trade.py"
        st.cache_data.clear()
        with patch("market_data.load_program_trade_cache", return_value=payload):
            app = AppTest.from_file(str(page), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertEqual([metric.value for metric in app.metric], ["10억원", "50억원", "50억원"])
            self.assertTrue(any("기준일 2026-09-18" in caption.value and "KIS test" in caption.value for caption in app.caption))
            self.assertEqual(len(app.get("plotly_chart")), 1)
            self.assertEqual(app.expander[0].label, "일별 상세 내역")
            self.assertFalse(app.expander[0].proto.expanded)
            self.assertEqual(len(app.dataframe), 1)
            app.radio[0].set_value("KOSDAQ").run()
            self.assertFalse(app.exception)
            self.assertEqual([metric.value for metric in app.metric], ["-5억원", "-25억원", "-25억원"])
        st.cache_data.clear()


if __name__ == "__main__":
    unittest.main()
