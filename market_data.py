from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

URL_BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))
DATA_DIR = Path(__file__).parent / "data"
PROGRAM_TRADE_CACHE_FILE = DATA_DIR / "program_trade_cache.json"
US_LIQUIDITY_CACHE_FILE = DATA_DIR / "us_liquidity_cache.json"


def cache_file_version(path: Path) -> tuple[int, int]:
    """Return a cache key that changes whenever a deployed JSON file changes."""
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return 0, 0


FRED_SERIES = {
    "tga": {
        "series_id": "WDTGAL",
        "label": "미국 재무부 TGA",
        "frequency": "주간 · 수요일",
        "source_unit": "백만 달러",
        "scale_to_billions": 0.001,
        "liquidity_relation": "inverse",
        "relation_label": "역방향",
        "relation_summary": "상승 → 유동성 흡수 · 하락 → 유동성 공급",
        "interpretation": "재무부 현금이 TGA에 쌓이면 은행 준비금을 줄이는 압력이 생깁니다.",
    },
    "m2": {
        "series_id": "M2SL",
        "label": "미국 M2",
        "frequency": "월간",
        "source_unit": "십억 달러",
        "scale_to_billions": 1.0,
        "liquidity_relation": "direct",
        "relation_label": "정방향",
        "relation_summary": "상승 → 통화 유동성 확대 · 하락 → 축소",
        "interpretation": "M2는 현금·예금·소액 정기예금·소매 MMF를 포괄하는 월간 광의통화입니다.",
    },
    "reverse_repo": {
        "series_id": "RRPONTSYD",
        "label": "연준 Overnight Reverse Repo",
        "frequency": "일간",
        "source_unit": "십억 달러",
        "scale_to_billions": 1.0,
        "liquidity_relation": "inverse",
        "relation_label": "역방향",
        "relation_summary": "상승 → 연준으로 자금 흡수 · 하락 → 시장 이동 여지",
        "interpretation": "역레포 감소분이 위험자산으로 곧바로 유입된다는 뜻은 아닙니다.",
    },
    "reserve_balances": {
        "series_id": "WRESBAL",
        "label": "연준 지급준비금",
        "frequency": "주간 · 수요일",
        "source_unit": "백만 달러",
        "scale_to_billions": 0.001,
        "liquidity_relation": "direct",
        "relation_label": "정방향",
        "relation_summary": "상승 → 은행권 유동성 확대 · 하락 → 축소",
        "interpretation": "지급준비금은 금융시스템의 즉시 사용 가능한 유동성이지만 주가와 일대일 신호는 아닙니다.",
    },
}


def classify_liquidity_effect(change: float, relation: str) -> str:
    if change == 0:
        return "변화 없음"
    multiplier = -1 if relation == "inverse" else 1
    return "유동성 확대 방향" if change * multiplier > 0 else "유동성 축소 방향"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            json.dump(
                payload,
                temporary_file,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def load_program_trade_cache(path: Path = PROGRAM_TRADE_CACHE_FILE) -> dict:
    return _load_json(path)


def save_program_trade_cache(
    payload: dict,
    path: Path = PROGRAM_TRADE_CACHE_FILE,
) -> None:
    _save_json_atomic(payload, path)


def load_us_liquidity_cache(path: Path = US_LIQUIDITY_CACHE_FILE) -> dict:
    return _load_json(path)


def save_us_liquidity_cache(
    payload: dict,
    path: Path = US_LIQUIDITY_CACHE_FILE,
) -> None:
    _save_json_atomic(payload, path)


def _number(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def normalize_program_trade_rows(rows: list[dict]) -> list[dict]:
    """Keep the daily program-trade fields used by the dashboard.

    KIS reports transaction amounts in millions of won. Dashboard cache values
    are converted to 억원 by dividing by 100.
    """

    normalized = []
    for row in rows:
        raw_date = str(row.get("stck_bsop_date", "")).strip()
        if len(raw_date) != 8 or not raw_date.isdigit():
            continue
        non_arbitrage = _number(row.get("nabt_smtn_ntby_tr_pbmn")) / 100.0
        arbitrage = _number(row.get("arbt_smtn_ntby_tr_pbmn")) / 100.0
        normalized.append(
            {
                "date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}",
                "non_arbitrage_net_억원": non_arbitrage,
                "arbitrage_net_억원": arbitrage,
                "total_program_net_억원": non_arbitrage + arbitrage,
            }
        )
    return sorted(normalized, key=lambda item: item["date"])


def fetch_program_trade_daily(
    market_code: str,
    access_token: str,
    app_key: str,
    app_secret: str,
    start_date: str,
    end_date: str,
    *,
    request_get: Callable = requests.get,
) -> list[dict]:
    if market_code not in {"K", "Q"}:
        raise ValueError("시장 코드는 K(코스피) 또는 Q(코스닥)여야 합니다.")
    response = request_get(
        f"{URL_BASE}/uapi/domestic-stock/v1/quotations/comp-program-trade-daily",
        headers={
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHPPG04600001",
            "custtype": "P",
        },
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_MRKT_CLS_CODE": market_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("rt_cd", "0")) != "0":
        raise RuntimeError(payload.get("msg1") or "프로그램매매 API 조회 실패")
    output = payload.get("output", [])
    if isinstance(output, dict):
        output = [output]
    return normalize_program_trade_rows(output)


def build_program_trade_cache(
    access_token: str,
    app_key: str,
    app_secret: str,
    *,
    now: datetime | None = None,
    target_date: str | None = None,
    request_get: Callable = requests.get,
) -> dict:
    current = now.astimezone(KST) if now else datetime.now(KST)
    if target_date:
        target = datetime.strptime(target_date, "%Y%m%d").replace(tzinfo=KST)
    else:
        target = current
    end_date = target.strftime("%Y%m%d")
    start_date = (target - timedelta(days=400)).strftime("%Y%m%d")
    markets = {}
    for market_key, market_code, label in (
        ("kospi", "K", "KOSPI"),
        ("kosdaq", "Q", "KOSDAQ"),
    ):
        markets[market_key] = {
            "label": label,
            "rows": fetch_program_trade_daily(
                market_code,
                access_token,
                app_key,
                app_secret,
                start_date,
                end_date,
                request_get=request_get,
            ),
        }
    return {
        "generated_at_kst": current.isoformat(),
        "source": "KIS 국내주식-115 · FHPPG04600001",
        "unit": "억원",
        "markets": markets,
    }


def program_cache_has_target_date(cache: dict, target_date: str) -> bool:
    expected = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    markets = cache.get("markets", {}) if isinstance(cache, dict) else {}
    for market_key in ("kospi", "kosdaq"):
        rows = markets.get(market_key, {}).get("rows", [])
        if not rows or rows[-1].get("date") != expected:
            return False
    return True


def fetch_fred_series(
    series_id: str,
    *,
    request_get: Callable = requests.get,
) -> pd.DataFrame:
    response = request_get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": series_id},
        timeout=30,
    )
    response.raise_for_status()
    frame = pd.read_csv(StringIO(response.text))
    if frame.shape[1] < 2:
        raise RuntimeError(f"FRED {series_id} 응답에 값 열이 없습니다.")
    frame = frame.iloc[:, :2].copy()
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame.dropna().sort_values("date")


def build_us_liquidity_cache(
    *,
    now: datetime | None = None,
    request_get: Callable = requests.get,
) -> dict:
    current = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    output = {}
    for key, metadata in FRED_SERIES.items():
        frame = fetch_fred_series(
            metadata["series_id"],
            request_get=request_get,
        )
        scale = metadata["scale_to_billions"]
        rows = [
            {
                "date": row.date.strftime("%Y-%m-%d"),
                "value_십억달러": float(row.value) * scale,
            }
            for row in frame.itertuples(index=False)
        ]
        output[key] = {
            "series_id": metadata["series_id"],
            "label": metadata["label"],
            "frequency": metadata["frequency"],
            "unit": "십억 달러",
            "liquidity_relation": metadata["liquidity_relation"],
            "relation_label": metadata["relation_label"],
            "relation_summary": metadata["relation_summary"],
            "interpretation": metadata["interpretation"],
            "source_url": (
                f"https://fred.stlouisfed.org/series/{metadata['series_id']}"
            ),
            "rows": rows,
        }
    return {
        "generated_at_utc": current.isoformat(),
        "source": "Federal Reserve Economic Data (FRED)",
        "series": output,
    }
