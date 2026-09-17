"""Validated KOSPI/KOSDAQ market-cap universes from KIS Open API."""

from dataclasses import dataclass
import math
import re
import time
from typing import Callable

import requests

from trading_calendar import completed_session, is_session_date


KIS_URL_BASE = "https://openapi.koreainvestment.com:9443"
MARKET_CAP_PATH = "/uapi/domestic-stock/v1/ranking/market-cap"
MARKET_CAP_TR_ID = "FHPST01740000"
MAX_CONTINUATION_PAGES = 30
CONTINUATION_DELAY_SECONDS = 0.1
STOCK_UNIVERSE_SOURCE = "Korea Investment & Securities Open API"
MARKETS = (
    ("kospi", "0001", 200),
    ("kosdaq", "1001", 150),
)


@dataclass(frozen=True)
class StockUniverse:
    kospi: dict[str, str]
    kosdaq: dict[str, str]
    all_symbols: dict[str, str]
    metadata: dict


def _validated_page_rows(rows: object, market: str) -> list[tuple[int, int, str, str]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"KIS {market} 시가총액 응답이 비어 있습니다.")

    validated = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"KIS {market} 시가총액 응답 형식이 잘못됐습니다.")
        code = str(row.get("mksc_shrn_iscd", "")).strip()
        name = str(row.get("hts_kor_isnm", "")).strip()
        try:
            rank = int(str(row.get("data_rank", "")).strip())
            market_cap = int(str(row.get("stck_avls", "")).replace(",", "").strip())
        except ValueError as exc:
            raise ValueError(f"KIS {market} 순위 또는 시가총액이 잘못됐습니다.") from exc
        if not re.fullmatch(r"[0-9A-Z]{6}", code):
            raise ValueError(f"KIS {market} 종목 코드 형식이 잘못됐습니다: {code}")
        if not name:
            raise ValueError(f"KIS {market} 종목명이 비어 있습니다.")
        if rank <= 0 or market_cap <= 0 or not math.isfinite(float(market_cap)):
            raise ValueError(f"KIS {market} 순위 또는 시가총액이 유효하지 않습니다.")
        validated.append((rank, market_cap, name, code))
    return validated


def _request_market(
    market: str,
    market_code: str,
    limit: int,
    access_token: str,
    app_key: str,
    app_secret: str,
    request_get: Callable,
    sleep: Callable[[float], None],
) -> tuple[dict[str, str], int]:
    rows: list[tuple[int, int, str, str]] = []
    seen_codes: set[str] = set()
    page_signatures: set[tuple[str, ...]] = set()
    continuation = ""

    for page_number in range(1, MAX_CONTINUATION_PAGES + 1):
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": MARKET_CAP_TR_ID,
            "custtype": "P",
            "tr_cont": continuation,
        }
        params = {
            "fid_input_price_2": "",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0",
            "fid_input_iscd": market_code,
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_vol_cnt": "",
        }
        response = request_get(
            f"{KIS_URL_BASE}{MARKET_CAP_PATH}",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("rt_cd")) != "0":
            message = payload.get("msg1") if isinstance(payload, dict) else None
            raise RuntimeError(message or f"KIS {market} 시가총액 조회에 실패했습니다.")

        page_rows = _validated_page_rows(payload.get("output"), market)
        signature = tuple(value[3] for value in page_rows)
        if signature in page_signatures:
            raise RuntimeError(f"KIS {market} 연속조회가 같은 페이지를 반복했습니다.")
        page_signatures.add(signature)
        for row in page_rows:
            if row[3] in seen_codes:
                raise ValueError(f"KIS {market} 종목 코드가 중복됐습니다: {row[3]}")
            rows.append(row)
            seen_codes.add(row[3])

        if len(rows) >= limit:
            break
        response_continuation = str(
            response.headers.get("tr_cont")
            or response.headers.get("tr-cont")
            or ""
        ).strip().upper()
        # KIS uses F (first page with more data) or M (middle page with
        # more data) depending on the gateway/API version. Both continue
        # with an N request, as in KIS's current official samples.
        if response_continuation not in {"F", "M"}:
            raise RuntimeError(
                f"KIS {market} 시가총액 목록이 불완전합니다: {len(rows)}/{limit} "
                f"(tr_cont={response_continuation or 'empty'})"
            )
        continuation = "N"
        sleep(CONTINUATION_DELAY_SECONDS)
    else:
        raise RuntimeError(
            f"KIS {market} 연속조회가 {MAX_CONTINUATION_PAGES}페이지를 초과했습니다."
        )

    # KIS returns this endpoint in market-cap order. Preserve continuation page
    # order so a final page that extends past the limit cannot reorder results.
    ranked = rows[:limit]
    market_caps = [value[1] for value in ranked]
    if any(left < right for left, right in zip(market_caps, market_caps[1:])):
        raise ValueError(f"KIS {market} 시가총액 순서가 올바르지 않습니다.")
    symbols = {name: code for _, _, name, code in ranked}
    if len(symbols) != limit or len(set(symbols.values())) != limit:
        raise ValueError(f"KIS {market} 선정 종목 수를 검증하지 못했습니다.")
    return symbols, page_number


def get_stock_universe(
    access_token: str,
    app_key: str,
    app_secret: str,
    target_date: str | None = None,
    *,
    request_get: Callable | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> StockUniverse:
    """Return the top 200 KOSPI and top 150 KOSDAQ symbols from KIS."""
    if not access_token or not app_key or not app_secret:
        raise ValueError("KIS 시가총액 조회에 인증 정보가 필요합니다.")
    target = target_date or completed_session()
    if not is_session_date(target):
        raise ValueError(f"목표일이 KRX 거래일이 아닙니다: {target}")

    get = request_get or requests.get
    selected: dict[str, dict[str, str]] = {}
    page_counts: dict[str, int] = {}
    for market, market_code, limit in MARKETS:
        selected[market], page_counts[market] = _request_market(
            market,
            market_code,
            limit,
            access_token,
            app_key,
            app_secret,
            get,
            sleep,
        )

    all_symbols = {**selected["kospi"], **selected["kosdaq"]}
    all_codes = list(selected["kospi"].values()) + list(selected["kosdaq"].values())
    if len(all_symbols) != 350 or len(set(all_codes)) != 350:
        raise ValueError("KIS 전체 선정 목록에 중복 종목명 또는 코드가 있습니다.")
    return StockUniverse(
        selected["kospi"],
        selected["kosdaq"],
        all_symbols,
        {
            "as_of": target,
            "target_date": target,
            "age_sessions": 0,
            "source": STOCK_UNIVERSE_SOURCE,
            "endpoint": MARKET_CAP_PATH,
            "tr_id": MARKET_CAP_TR_ID,
            "pages": page_counts,
        },
    )
