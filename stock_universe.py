"""Bounded, dated listing retrieval from FinanceDataReader's own provider.

The provider's latest-day CSV can lag KRX's latest business date. Query dated
files directly, newest first, without confusing listing age with flow age.
"""
from dataclasses import dataclass
from io import StringIO
import math
import re
from typing import Callable

import pandas as pd
import requests

from trading_calendar import calendar_for_year, completed_session, is_session_date

LISTING_BASE_URL = (
    "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
    "refs/heads/master/data/listing/krx"
)
MAX_LISTING_SESSIONS = 5


@dataclass(frozen=True)
class StockUniverse:
    kospi: dict[str, str]
    kosdaq: dict[str, str]
    all_symbols: dict[str, str]
    metadata: dict


def parse_listing(text: str) -> tuple[dict, dict, dict]:
    frame = pd.read_csv(StringIO(text), dtype={"Code": str, "Name": str, "MarketId": str})
    required = ["Code", "Name", "Marcap", "MarketId"]
    if not set(required).issubset(frame.columns) or frame.empty:
        raise ValueError("종목 목록 필수 스키마가 없습니다.")
    if frame[required].isna().any().any():
        raise ValueError("종목 목록 필수값이 누락됐습니다.")
    if not frame["Code"].map(lambda value: bool(re.fullmatch(r"[0-9A-Z]{6}", value))).all():
        raise ValueError("종목 코드 형식이 잘못됐습니다.")
    if not frame["Name"].str.strip().ne("").all():
        raise ValueError("종목명이 비어 있습니다.")
    if frame["Code"].duplicated().any() or frame["Name"].duplicated().any():
        raise ValueError("종목 목록에 중복 코드 또는 이름이 있습니다.")
    if not frame["MarketId"].isin({"STK", "KSQ", "KNX"}).all():
        raise ValueError("알 수 없는 시장 구분이 있습니다.")
    frame["Marcap"] = pd.to_numeric(frame["Marcap"], errors="coerce")
    if not frame["Marcap"].map(math.isfinite).all() or not frame["Marcap"].gt(0).all():
        raise ValueError("시가총액이 유효한 양수가 아닙니다.")

    maps = []
    for market_id, limit in (("STK", 200), ("KSQ", 150)):
        market = frame[frame["MarketId"] == market_id]
        if len(market) < limit:
            raise ValueError(f"시장 {market_id} 종목 목록이 불완전합니다: {len(market)}/{limit}")
        ranked = market.sort_values(["Marcap", "Code"], ascending=[False, True]).head(limit)
        symbols = dict(zip(ranked["Name"], ranked["Code"]))
        if len(symbols) != limit or len(set(symbols.values())) != limit:
            raise ValueError("선정 종목 수를 검증하지 못했습니다.")
        maps.append(symbols)
    return maps[0], maps[1], dict(zip(frame["Name"], frame["Code"]))


def get_stock_universe(
    target_date: str | None = None,
    *,
    request_get: Callable | None = None,
) -> StockUniverse:
    target = target_date or completed_session()
    if not is_session_date(target):
        raise ValueError(f"목표일이 KRX 거래일이 아닙니다: {target}")
    get = request_get or requests.get
    calendar = calendar_for_year(int(target[:4]))
    session = pd.Timestamp(target)
    last_error = "게시된 목록이 없습니다."
    for age in range(MAX_LISTING_SESSIONS):
        as_of = session.strftime("%Y%m%d")
        url = f"{LISTING_BASE_URL}/{session:%Y-%m-%d}.csv"
        try:
            response = get(url, timeout=20)
            response.raise_for_status()
            kospi, kosdaq, all_symbols = parse_listing(response.text)
            return StockUniverse(kospi, kosdaq, all_symbols, {
                "as_of": as_of, "target_date": target,
                "age_sessions": age, "source_url": url,
                "source": "FinanceData/fdr_krx_data_cache",
            })
        except (requests.RequestException, ValueError, pd.errors.ParserError) as exc:
            last_error = str(exc)
        session = calendar.previous_session(session)
    raise RuntimeError(
        f"최근 {MAX_LISTING_SESSIONS}거래일 안에 검증된 종목 목록이 없습니다. "
        f"기존 수급 캐시를 보존합니다. 마지막 오류: {last_error}"
    )
