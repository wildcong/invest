
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd
import requests

URL_BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))
CACHE_FILE = Path(__file__).parent / "data" / "scan_cache.json"
AUTO_REFRESH_PRIMARY_HOUR = 15
AUTO_REFRESH_PRIMARY_MINUTE = 45
AUTO_REFRESH_BACKUP_HOUR = 16
AUTO_REFRESH_BACKUP_MINUTE = 15


def get_target_date(now: Optional[datetime] = None) -> str:
    current = now.astimezone(KST) if now else datetime.now(KST)
    if current.hour < 15 or (current.hour == 15 and current.minute < 40):
        target = current - timedelta(days=1)
    else:
        target = current
    while target.weekday() > 4:
        target -= timedelta(days=1)
    return target.strftime("%Y%m%d")


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


def cache_has_target_date(cache: Dict, target_date: str) -> bool:
    if cache.get("target_date") != target_date:
        return False

    markets = cache.get("markets", {})
    required_keys = ("kospi200", "kosdaq150")
    for market_key in required_keys:
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
        market_size = market.get("market_size", 0)
        scanned = summary.get("scanned", 0)
        if isinstance(market_size, int) and market_size > 20:
            if scanned < int(market_size * 0.8):
                return False
            if len(chart_data) < int(scanned * 0.8):
                return False
    return True


def get_stock_lists():
    fallback_k200 = {"삼성전자": "005930"}
    fallback_kq150 = {"에코프로": "086520"}
    fallback_all = {**fallback_k200, **fallback_kq150}

    try:
        import FinanceDataReader as fdr
    except Exception:
        return fallback_k200, fallback_kq150, fallback_all

    def to_symbol_map(df: pd.DataFrame, limit: Optional[int] = None) -> Dict[str, str]:
        if df.empty:
            return {}
        mcap_col = "Marcap" if "Marcap" in df.columns else "MarCap" if "MarCap" in df.columns else None
        ranked = df.sort_values(mcap_col, ascending=False) if mcap_col else df
        if limit:
            ranked = ranked.head(limit)
        return dict(zip(ranked["Name"], ranked["Code"]))

    dict_k200 = fallback_k200
    dict_kq150 = fallback_kq150
    dict_all = fallback_all

    try:
        dict_k200 = to_symbol_map(fdr.StockListing("KOSPI"), limit=200) or fallback_k200
    except Exception:
        pass

    try:
        dict_kq150 = to_symbol_map(fdr.StockListing("KOSDAQ"), limit=150) or fallback_kq150
    except Exception:
        pass

    try:
        dict_all = to_symbol_map(fdr.StockListing("KRX")) or {**dict_k200, **dict_kq150}
    except Exception:
        dict_all = {**dict_k200, **dict_kq150}

    return dict_k200, dict_kq150, dict_all


def get_access_token(
    app_key: str,
    app_secret: str,
    *,
    request_post: Callable = requests.post,
) -> Optional[str]:
    """Issue one KIS access token for the daily batch.

    There is intentionally no retry or local token cache here. The scheduled
    batch is the sole caller, and all data requests in that run reuse the token.
    This guarantees at most one token-issuance request per workflow execution.
    """
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    try:
        response = request_post(
            f"{URL_BASE}/oauth2/tokenP",
            headers=headers,
            data=json.dumps(body),
            timeout=20,
        )
        response.raise_for_status()
        return response.json().get("access_token")
    except (requests.RequestException, ValueError, AttributeError):
        return None


def get_investor_data(ticker: str, access_token: str, app_key: str, app_secret: str) -> pd.DataFrame:
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
        "FID_INPUT_DATE_1": get_target_date(),
        "FID_ORG_ADJ_PRC": "",
        "FID_ETC_CLS_CODE": "1",
    }
    url = f"{URL_BASE}/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"

    try:
        res = requests.get(url, headers=headers, params=params, timeout=20)
        res_json = res.json()
        if res.status_code == 200 and "output2" in res_json:
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
                    df[normalized] = 0
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
            df["Date"] = pd.to_datetime(df["Date"])
            for col in ["Price", "Foreign_Amt", "Inst_Amt", "Personal_Amt"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna()
            df["F_억"] = df["Foreign_Amt"] / 100
            df["I_억"] = df["Inst_Amt"] / 100
            df["P_억"] = df["Personal_Amt"] / 100
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


def serialize_chart_data(df: pd.DataFrame, max_rows: int = 60):
    if df.empty:
        return []

    columns = ["Price", "F_억", "I_억", "P_억"]
    chart_df = df.tail(max_rows).copy()
    for column in columns:
        if column not in chart_df.columns:
            chart_df[column] = 0

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


def scan_market(stock_dict: Dict[str, str], access_token: str, app_key: str, app_secret: str):
    filtered_map = {}
    summary = {"buy": 0, "mixed": 0, "sell": 0, "scanned": 0}
    direction_groups = {"buy": [], "mixed": [], "sell": []}
    chart_data = {}

    for name, ticker in stock_dict.items():
        df = get_investor_data(ticker, access_token, app_key, app_secret)
        if df.empty or len(df) < 5:
            continue

        chart_data[ticker] = serialize_chart_data(df)
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


def build_scan_cache(app_key: str, app_secret: str, access_token: str):
    if not access_token:
        raise ValueError("일일 배치에서 발급한 KIS access token이 필요합니다.")
    dict_k200, dict_kq150, _ = get_stock_lists()
    generated_at = datetime.now(KST)
    target_date = get_target_date(generated_at)

    kospi_filtered, kospi_summary, kospi_groups, kospi_chart_data = scan_market(
        dict_k200,
        access_token,
        app_key,
        app_secret,
    )
    kosdaq_filtered, kosdaq_summary, kosdaq_groups, kosdaq_chart_data = scan_market(
        dict_kq150,
        access_token,
        app_key,
        app_secret,
    )

    return {
        "generated_at_kst": generated_at.isoformat(),
        "target_date": target_date,
        "markets": {
            "kospi200": {
                "label": "KOSPI 200",
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
                "label": "KOSDAQ 150",
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
