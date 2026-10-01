import unittest
from unittest.mock import patch

import pandas as pd

from stock_detail import StockDetailUnavailable, load_stock_detail


class StockDetailTests(unittest.TestCase):
    @staticmethod
    def chart(rows: int = 5) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Price": [100.0] * rows,
                "F_억": [1.0] * rows,
                "I_억": [2.0] * rows,
                "P_억": [-3.0] * rows,
            },
            index=pd.bdate_range(end="2026-08-28", periods=rows),
        )

    def test_reuses_existing_batch_token_for_one_selected_stock(self):
        with (
            patch("stock_detail.load_batch_state", return_value={}) as state,
            patch("stock_detail._reusable_token", return_value="stored-token") as token,
            patch("stock_detail.get_investor_data", return_value=self.chart()) as fetch,
        ):
            result = load_stock_detail("000020", "20260828", "key", "secret")
        self.assertEqual(len(result), 5)
        state.assert_called_once()
        self.assertEqual(token.call_args.args[1], "secret")
        fetch.assert_called_once_with("000020", "stored-token", "key", "secret", "20260828")

    def test_expired_or_missing_token_never_starts_a_second_issuance(self):
        with (
            patch("stock_detail.load_batch_state", return_value={}),
            patch("stock_detail._reusable_token", return_value=None),
            patch("stock_detail.get_investor_data") as fetch,
        ):
            with self.assertRaisesRegex(StockDetailUnavailable, "재사용 가능한 KIS 토큰"):
                load_stock_detail("000020", "20260828", "key", "secret")
        fetch.assert_not_called()

    def test_invalid_or_incomplete_chart_is_not_displayed(self):
        with (
            patch("stock_detail.load_batch_state", return_value={}),
            patch("stock_detail._reusable_token", return_value="stored-token"),
            patch("stock_detail.get_investor_data", return_value=self.chart(4)),
        ):
            with self.assertRaisesRegex(StockDetailUnavailable, "유효하지 않습니다"):
                load_stock_detail("000020", "20260828", "key", "secret")


if __name__ == "__main__":
    unittest.main()
