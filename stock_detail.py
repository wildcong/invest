"""Read-only, on-demand KIS flow chart for a searched stock."""

from datetime import datetime

import pandas as pd

from market_data import KST
from prefetch_scan_cache import _reusable_token, load_batch_state
from scanner import (
    INVESTOR_CHART_MAX_ROWS,
    get_investor_data,
    serialize_chart_data,
    valid_chart_rows,
)


class StockDetailUnavailable(RuntimeError):
    """The selected stock cannot be displayed without a new token request."""


def load_stock_detail(
    ticker: str,
    target_date: str,
    app_key: str,
    app_secret: str,
    *,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Reuse the batch token; never mint a second token from a page view."""
    if not app_key or not app_secret:
        raise StockDetailUnavailable("Streamlit에 KIS 조회 키가 설정되지 않았습니다.")

    try:
        token = _reusable_token(
            load_batch_state(), app_secret, now or datetime.now(KST)
        )
    except RuntimeError as exc:
        raise StockDetailUnavailable("저장된 KIS 토큰을 읽을 수 없습니다.") from exc
    if not token:
        raise StockDetailUnavailable(
            "재사용 가능한 KIS 토큰이 없습니다. 일일 배치나 수동 갱신 후 다시 조회해 주세요."
        )

    try:
        frame = get_investor_data(ticker, token, app_key, app_secret, target_date)
    except Exception as exc:
        raise StockDetailUnavailable("KIS 종목별 수급 조회에 실패했습니다.") from exc
    if frame.empty:
        raise StockDetailUnavailable("KIS에서 이 종목의 수급 자료를 받지 못했습니다.")

    frame = frame.tail(INVESTOR_CHART_MAX_ROWS)
    if not valid_chart_rows(serialize_chart_data(frame), target_date):
        raise StockDetailUnavailable("KIS 수급 자료의 날짜나 값이 유효하지 않습니다.")
    return frame
