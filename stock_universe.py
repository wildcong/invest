"""Validated KOSPI/KOSDAQ market-cap universes from official KIS masters."""

from dataclasses import dataclass
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
import re
from typing import Callable
from zipfile import BadZipFile, ZipFile

import requests

from trading_calendar import completed_session, is_session_date


KST = timezone(timedelta(hours=9))
KIS_MASTER_URL_BASE = "https://new.real.download.dws.co.kr/common/master"
STOCK_UNIVERSE_SOURCE = "Korea Investment & Securities official master files"

# Fixed-width layouts published in KIS's official open-trading-api repository.
# Both layouts place the previous-session market cap in the fifth field from
# the end. The line prefix is short code (9), standard code (12), then name.
KOSPI_FIELD_WIDTHS = (
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1,
    1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 1,
    9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
)
KOSDAQ_FIELD_WIDTHS = (
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3,
    1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8,
    9, 3, 1, 1, 1,
)
MARKETS = (
    ("kospi", 200, KOSPI_FIELD_WIDTHS),
    ("kosdaq", 150, KOSDAQ_FIELD_WIDTHS),
)


@dataclass(frozen=True)
class StockUniverse:
    kospi: dict[str, str]
    kosdaq: dict[str, str]
    all_symbols: dict[str, str]
    metadata: dict


def _split_fixed_width(value: str, widths: tuple[int, ...]) -> list[str]:
    fields = []
    offset = 0
    for width in widths:
        fields.append(value[offset : offset + width])
        offset += width
    return fields


def _parse_master(
    archive: bytes,
    market: str,
    limit: int,
    widths: tuple[int, ...],
) -> dict[str, str]:
    expected_name = f"{market}_code.mst"
    try:
        with ZipFile(BytesIO(archive)) as zipped:
            if expected_name not in zipped.namelist():
                raise ValueError(f"KIS {market} 마스터 파일이 압축에 없습니다.")
            raw = zipped.read(expected_name)
    except BadZipFile as exc:
        raise ValueError(f"KIS {market} 마스터 압축 형식이 잘못됐습니다.") from exc

    try:
        lines = raw.decode("cp949").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"KIS {market} 마스터 문자 인코딩이 잘못됐습니다.") from exc

    tail_width = sum(widths)
    ranked: list[tuple[int, str, str]] = []
    seen_codes: set[str] = set()
    for line in lines:
        if len(line) <= tail_width + 21:
            raise ValueError(f"KIS {market} 마스터 행 길이가 잘못됐습니다.")
        prefix = line[:-tail_width]
        values = _split_fixed_width(line[-tail_width:], widths)
        code = prefix[:9].strip()
        name = prefix[21:].strip()
        security_group = values[0].strip()

        # ST is KIS's stock group. This excludes funds, ETFs, ETNs, ELWs,
        # beneficiary certificates, and other non-stock master rows.
        if security_group != "ST":
            continue
        if not re.fullmatch(r"[0-9A-Z]{6}", code):
            raise ValueError(f"KIS {market} 종목 코드 형식이 잘못됐습니다: {code}")
        if not name:
            raise ValueError(f"KIS {market} 종목명이 비어 있습니다.")
        if code in seen_codes:
            raise ValueError(f"KIS {market} 종목 코드가 중복됐습니다: {code}")
        seen_codes.add(code)
        try:
            market_cap = int(values[-5].strip())
        except ValueError as exc:
            raise ValueError(f"KIS {market} 시가총액이 잘못됐습니다: {code}") from exc
        if market_cap > 0:
            ranked.append((market_cap, code, name))

    ranked.sort(key=lambda row: (-row[0], row[1]))
    if len(ranked) < limit:
        raise RuntimeError(
            f"KIS {market} 마스터의 유효 주식이 부족합니다: {len(ranked)}/{limit}"
        )
    selected = ranked[:limit]
    symbols = {name: code for _, code, name in selected}
    if len(symbols) != limit or len(set(symbols.values())) != limit:
        raise ValueError(f"KIS {market} 선정 종목 수를 검증하지 못했습니다.")
    return symbols


def _master_as_of(response: object, market: str, target_date: str) -> str:
    last_modified = str(response.headers.get("Last-Modified") or "").strip()
    try:
        modified = parsedate_to_datetime(last_modified)
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        as_of = modified.astimezone(KST).strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"KIS {market} 마스터 갱신시각을 확인할 수 없습니다.") from exc
    if as_of < target_date:
        raise RuntimeError(
            f"KIS {market} 마스터가 아직 목표일 자료가 아닙니다: {as_of}/{target_date}"
        )
    return as_of


def get_stock_universe(
    access_token: str,
    app_key: str,
    app_secret: str,
    target_date: str | None = None,
    *,
    request_get: Callable | None = None,
    sleep: Callable[[float], None] | None = None,
) -> StockUniverse:
    """Return the top 200 KOSPI and top 150 KOSDAQ stocks from KIS."""
    del sleep
    if not access_token or not app_key or not app_secret:
        raise ValueError("KIS 시가총액 조회에 인증 정보가 필요합니다.")
    target = target_date or completed_session()
    if not is_session_date(target):
        raise ValueError(f"목표일이 KRX 거래일이 아닙니다: {target}")

    get = request_get or requests.get
    selected: dict[str, dict[str, str]] = {}
    master_dates: dict[str, str] = {}
    for market, limit, widths in MARKETS:
        url = f"{KIS_MASTER_URL_BASE}/{market}_code.mst.zip"
        response = get(url, timeout=30)
        response.raise_for_status()
        master_dates[market] = _master_as_of(response, market, target)
        selected[market] = _parse_master(response.content, market, limit, widths)

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
            "endpoints": {
                market: f"{KIS_MASTER_URL_BASE}/{market}_code.mst.zip"
                for market, _, _ in MARKETS
            },
            "master_dates": master_dates,
        },
    )
