
import json
import os
import tempfile
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd
import requests

from trading_calendar import completed_session, is_session_date
from stock_universe import get_stock_universe

URL_BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))
CACHE_FILE = Path(__file__).parent / "data" / "scan_cache.json"
INVESTOR_CHART_MAX_ROWS = 30
AUTO_REFRESH_PRIMARY_HOUR = 15
AUTO_REFRESH_PRIMARY_MINUTE = 45
AUTO_REFRESH_BACKUP_HOUR = 16
AUTO_REFRESH_BACKUP_MINUTE = 15


def get_target_date(now: Optional[datetime] = None) -> str:
    return completed_session(now)


def get_auto_refresh_window(now: Optional[datetime] = None):
    current = now.astimezone(KST) if now else datetime.now(KST)
    primary = current.replace(
        hour=AUTO_REFRESH_PRIMARY_HOUR,
        minute=AUTO_REFRESH_PRIMARY_MINUTE,
        second=0,
        microsecond=0,
    )
    backup = current.replace(
        hour=AUTO_REFRESH_BACKUP_HOUR,
        minute=AUTO_REFRESH_BACKUP_MINUTE,
        second=0,
        microsecond=0,
    )
    return primary, backup


def scan_coverage(market: Dict, target_date: str, expected_size: int) -> dict:
    symbols = market.get("symbols", {})
    charts = market.get("chart_data", {})
    current = 0
    stale = []
    missing = []
    invalid = []
    expected = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    for name, ticker in symbols.items():
        rows = charts.get(ticker, [])
        if not isinstance(rows, list):
            invalid.append(name)
        elif len(rows) < 5:
            missing.append(name)
        elif not valid_chart_rows(rows, target_date):
            invalid.append(name)
        elif rows[-1].get("Date") != expected:
            stale.append(name)
        else:
            current += 1
    return {"expected": expected_size, "current": current, "stale": stale, "invalid": invalid,
            "missing": missing, "universe_valid": len(symbols) == expected_size
            and len(set(symbols.values())) == expected_size}


def valid_chart_rows(rows: list[dict], target_date: str) -> bool:
    """Only actual, ordered session observations can count as fresh data."""
    if len(rows) < 5:
        return False
    previous_date = ""
    for row in rows:
        try:
            date = datetime.strptime(row["Date"], "%Y-%m-%d")
            date_key = date.strftime("%Y%m%d")
            if (date.strftime("%Y-%m-%d") != row["Date"] or
                    date_key <= previous_date or date_key > target_date or
                    not is_session_date(date_key)):
                return False
            values = [row[key] for key in ("Price", "F_억", "I_억", "P_억")]
            if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in values):
                return False
            if float(row["Price"]) <= 0:
                return False
            previous_date = date_key
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
    return True


def cache_has_target_date(cache: Dict, target_date: str, *, allow_partial: bool = False) -> bool:
    if not is_session_date(target_date):
        return False
    if cache.get("target_date") != target_date:
        return False
    universe = cache.get("universe", {})
    if universe and not allow_partial and universe.get("as_of") != target_date:
        return False

    markets = cache.get("markets", {})
    for market_key, expected_size in (("kospi200", 200), ("kosdaq150", 150)):
        market = markets.get(market_key, {})
        if market.get("target_date") != target_date:
            return False
        summary = market.get("summary", {})
        if not summary:
            return False
        if not market.get("direction_groups"):
            return False
        chart_data = market.get("chart_data", {})
        if not isinstance(chart_data, dict) or not chart_data:
            return False
        coverage = scan_coverage(market, target_date, expected_size)
        minimum = int(expected_size * 0.8) if allow_partial else expected_size
        if not coverage["universe_valid"] or coverage["current"] < minimum:
            return False
    return True


def get_stock_lists():
    """Preserve the public three-map interface for the search UI."""
    universe = get_stock_universe()
    return universe.kospi, universe.kosdaq, universe.all_symbols


def get_access_token(
    app_key: str,
    app_secret: str,
    *,
    request_post: Callable = requests.post,
) -> Optional[str]:
    """Compatibility wrapper. Daily batches use the persisted token manager."""
    try:
        return issue_access_token(
            app_key,
            app_secret,
            request_post=request_post,
        )["access_token"]
    except (
        requests.RequestException,
        RuntimeError,
        ValueError,
        AttributeError,
        KeyError,
    ):
        return None


def issue_access_token(
    app_key: str,
    app_secret: str,
    *,
    request_post: Callable = requests.post,
) -> dict:
    """Issue a KIS token and retain the official expiry metadata.

    The batch persists ``access_token_token_expired`` so later scheduled runs
    can reuse the token until its real expiry instead of guessing from a
    calendar-day guard.
    """

    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }
    response = request_post(
        f"{URL_BASE}/oauth2/tokenP",
        headers=headers,
        data=json.dumps(body),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        message = payload.get("msg1") if isinstance(payload, dict) else None
        raise RuntimeError(message or "KIS 토큰 응답에 access_token이 없습니다.")
    return payload


def get_investor_data(ticker: str, access_token: str, app_key: str, app_secret: str, target_date: str | None = None) -> pd.DataFrame:
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHPTJ04160001",
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": target_date or get_target_date(),
        "FID_ORG_ADJ_PRC": "",
        "FID_ETC_CLS_CODE": "1",
    }
    url = f"{URL_BASE}/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"

    try:
        res = requests.get(url, headers=headers, params=params, timeout=20)
        res_json = res.json()
        if res.status_code == 200 and str(res_json.get("rt_cd")) == "0" and "output2" in res_json:
            df = pd.DataFrame(res_json["output2"])
            if df.empty:
                return pd.DataFrame()
            column_aliases = {
                "stck_bsop_date": ["stck_bsop_date", "STCK_BSOP_DATE"],
                "stck_clpr": ["stck_clpr", "STCK_CLPR"],
                "frgn_ntby_tr_pbmn": ["frgn_ntby_tr_pbmn", "FRGN_NTBY_TR_PBMN"],
                "orgn_ntby_tr_pbmn": ["orgn_ntby_tr_pbmn", "ORGN_NTBY_TR_PBMN"],
                "prsn_ntby_tr_pbmn": ["prsn_ntby_tr_pbmn", "PRSN_NTBY_TR_PBMN"],
            }
            for normalized, candidates in column_aliases.items():
                source_col = next((candidate for candidate in candidates if candidate in df.columns), None)
                if source_col and source_col != normalized:
                    df[normalized] = df[source_col]
                elif not source_col:
                    return pd.DataFrame()
            df = df[
                [
                    "stck_bsop_date",
                    "stck_clpr",
                    "frgn_ntby_tr_pbmn",
                    "orgn_ntby_tr_pbmn",
                    "prsn_ntby_tr_pbmn",
                ]
            ].copy()
            df.columns = ["Date", "Price", "Foreign_Amt", "Inst_Amt", "Personal_Amt"]
            df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d", errors="coerce")
            for col in ["Price", "Foreign_Amt", "Inst_Amt", "Personal_Amt"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            # Dropping a malformed observation would silently change the
            # five-day signal window. Reject the entire response instead.
            if df.isna().any().any():
                return pd.DataFrame()
            numeric_columns = ["Price", "Foreign_Amt", "Inst_Amt", "Personal_Amt"]
            if (df["Price"] <= 0).any() or not all(
                df[column].map(math.isfinite).all() for column in numeric_columns
            ):
                return pd.DataFrame()
            df["F_억"] = df["Foreign_Amt"] / 100
            df["I_억"] = df["Inst_Amt"] / 100
            df["P_억"] = df["Personal_Amt"] / 100
            df = df[df["Date"] <= pd.Timestamp(target_date or get_target_date())]
            if df["Date"].duplicated().any():
                return pd.DataFrame()
            return df.sort_values("Date").set_index("Date")
    except Exception:
        pass
    return pd.DataFrame()


def classify_5day_direction(df: pd.DataFrame) -> str:
    f_sum = df["F_억"].tail(5).sum()
    i_sum = df["I_억"].tail(5).sum()
    if f_sum > 0 and i_sum > 0:
        return "buy"
    if f_sum < 0 and i_sum < 0:
        return "sell"
    return "mixed"


def summarize_5day_flow(df: pd.DataFrame) -> Dict[str, float]:
    foreign_5d = round(df["F_억"].tail(5).sum(), 1)
    inst_5d = round(df["I_억"].tail(5).sum(), 1)
    total_5d = round(foreign_5d + inst_5d, 1)
    strength = round(abs(foreign_5d) + abs(inst_5d), 1)
    return {
        "foreign_5d": foreign_5d,
        "inst_5d": inst_5d,
        "total_5d": total_5d,
        "strength": strength,
    }


def serialize_chart_data(df: pd.DataFrame, max_rows: int = INVESTOR_CHART_MAX_ROWS):
    if df.empty:
        return []

    columns = ["Price", "F_억", "I_억", "P_억"]
    chart_df = df.tail(max_rows).copy()
    for column in columns:
        if column not in chart_df.columns:
            raise ValueError(f"차트 원자료 필드 누락: {column}")

    rows = []
    for index, row in chart_df[columns].iterrows():
        rows.append(
            {
                "Date": index.strftime("%Y-%m-%d"),
                "Price": float(row["Price"]),
                "F_억": float(row["F_억"]),
                "I_억": float(row["I_억"]),
                "P_억": float(row["P_억"]),
            }
        )
    return rows


def scan_market(stock_dict: Dict[str, str], access_token: str, app_key: str, app_secret: str, target_date: str | None = None, *, reuse_chart_data: Dict | None = None):
    filtered_map = {}
    summary = {"buy": 0, "mixed": 0, "sell": 0, "scanned": 0}
    direction_groups = {"buy": [], "mixed": [], "sell": []}
    chart_data = {}
    target_date = target_date or get_target_date()
    expected_date = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"

    for name, ticker in stock_dict.items():
        cached = (reuse_chart_data or {}).get(ticker, [])
        if (isinstance(cached, list) and valid_chart_rows(cached, target_date)
                and cached[-1]["Date"] == expected_date):
            df = pd.DataFrame(cached).set_index("Date")
            df.index = pd.to_datetime(df.index)
            df = df[["Price", "F_억", "I_억", "P_억"]].apply(pd.to_numeric)
        else:
            df = get_investor_data(ticker, access_token, app_key, app_secret, target_date)
        if df.empty or len(df) < 5:
            continue

        rows = serialize_chart_data(df)
        if not valid_chart_rows(rows, target_date or get_target_date()):
            continue
        chart_data[ticker] = rows
        direction = classify_5day_direction(df)
        flow = summarize_5day_flow(df)
        summary["scanned"] += 1
        summary[direction] += 1
        label = name
        if direction == "buy":
            label = f"{name} (↑↑)"
        elif direction == "sell":
            label = f"{name} (↓↓)"
        direction_groups[direction].append(
            {
                "name": name,
                "ticker": ticker,
                "label": label,
                "as_of": df.index[-1].strftime("%Y-%m-%d"),
                **flow,
            }
        )

        if direction == "buy":
            filtered_map[name] = label
        elif direction == "sell":
            filtered_map[name] = label

    for direction in direction_groups:
        direction_groups[direction].sort(key=lambda item: item["strength"], reverse=True)

    return filtered_map, summary, direction_groups, chart_data


def build_scan_cache(app_key: str, app_secret: str, access_token: str, *, target_date: str | None = None, reuse_cache: Dict | None = None):
    if not access_token:
        raise ValueError("일일 배치에서 발급한 KIS access token이 필요합니다.")
    generated_at = datetime.now(KST)
    target_date = target_date or get_target_date(generated_at)
    universe = get_stock_universe(target_date)
    dict_k200, dict_kq150 = universe.kospi, universe.kosdaq
    reusable_charts = {}
    if isinstance(reuse_cache, dict) and reuse_cache.get("target_date") == target_date:
        for market in reuse_cache.get("markets", {}).values():
            if isinstance(market, dict) and market.get("target_date") == target_date:
                charts = market.get("chart_data", {})
                if isinstance(charts, dict):
                    reusable_charts.update(charts)

    kospi_filtered, kospi_summary, kospi_groups, kospi_chart_data = scan_market(
        dict_k200,
        access_token,
        app_key,
        app_secret,
        target_date,
        reuse_chart_data=reusable_charts,
    )
    kosdaq_filtered, kosdaq_summary, kosdaq_groups, kosdaq_chart_data = scan_market(
        dict_kq150,
        access_token,
        app_key,
        app_secret,
        target_date,
        reuse_chart_data=reusable_charts,
    )

    return {
        "generated_at_kst": generated_at.isoformat(),
        "target_date": target_date,
        "universe": universe.metadata,
        "markets": {
            "kospi200": {
                "label": "KOSPI 시가총액 상위 200",
                "market_size": len(dict_k200),
                "symbols": dict_k200,
                "filtered_map": kospi_filtered,
                "summary": kospi_summary,
                "direction_groups": kospi_groups,
                "chart_data": kospi_chart_data,
                "target_date": target_date,
                "generated_at_kst": generated_at.isoformat(),
            },
            "kosdaq150": {
                "label": "KOSDAQ 시가총액 상위 150",
                "market_size": len(dict_kq150),
                "symbols": dict_kq150,
                "filtered_map": kosdaq_filtered,
                "summary": kosdaq_summary,
                "direction_groups": kosdaq_groups,
                "chart_data": kosdaq_chart_data,
                "target_date": target_date,
                "generated_at_kst": generated_at.isoformat(),
            },
        },
    }


def attach_previous_market_snapshots(existing_cache: Dict, new_cache: Dict):
    existing_markets = existing_cache.get("markets", {}) if isinstance(existing_cache, dict) else {}
    new_markets = new_cache.get("markets", {}) if isinstance(new_cache, dict) else {}

    for market_key, market_payload in new_markets.items():
        existing_market = existing_markets.get(market_key, {})
        existing_target_date = existing_market.get("target_date")
        new_target_date = market_payload.get("target_date")

        if existing_target_date and existing_target_date != new_target_date:
            market_payload["previous_target_date"] = existing_target_date
            market_payload["previous_direction_groups"] = existing_market.get("direction_groups", {})
        else:
            market_payload["previous_target_date"] = existing_market.get("previous_target_date")
            market_payload["previous_direction_groups"] = existing_market.get("previous_direction_groups", {})

    return new_cache


def load_scan_cache(path: Path = CACHE_FILE):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_scan_cache(payload, path: Path = CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            json.dump(payload, temporary_file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
