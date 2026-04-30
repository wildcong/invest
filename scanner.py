
import json
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests

URL_BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))
CACHE_FILE = Path(__file__).parent / "data" / "scan_cache.json"
LEGACY_TOKEN_CACHE_FILE = Path(__file__).parent / "data" / "kis_token_cache.json"
TOKEN_CACHE_FILE = Path(os.environ.get("KIS_TOKEN_CACHE_FILE", "/tmp/kis_token_cache.json"))
AUTO_REFRESH_PRIMARY_HOUR = 16
AUTO_REFRESH_PRIMARY_MINUTE = 30
AUTO_REFRESH_BACKUP_HOUR = 17
AUTO_REFRESH_BACKUP_MINUTE = 5
TOKEN_EXPIRY_BUFFER_SECONDS = 300


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
        if not market.get("summary"):
            return False
        if not market.get("direction_groups"):
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


def get_token_cache_key(app_key: str, app_secret: str) -> str:
    raw = f"{app_key}:{app_secret}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_token_cache(path: Path = TOKEN_CACHE_FILE) -> Dict:
    for candidate in (path, LEGACY_TOKEN_CACHE_FILE):
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def save_token_cache(payload: Dict, path: Path = TOKEN_CACHE_FILE):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Token cache 저장 실패가 차트 전체를 막지 않도록 조용히 무시합니다.
        pass


def get_cached_access_token(app_key: str, app_secret: str, path: Path = TOKEN_CACHE_FILE) -> Optional[str]:
    cache = load_token_cache(path)
    if cache.get("cache_key") != get_token_cache_key(app_key, app_secret):
        return None

    access_token = cache.get("access_token")
    expires_at_raw = cache.get("expires_at")
    if not access_token or not expires_at_raw:
        return None

    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except ValueError:
        return None

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    if expires_at <= now_utc + timedelta(seconds=TOKEN_EXPIRY_BUFFER_SECONDS):
        return None

    return access_token


def get_access_token(app_key: str, app_secret: str, force_refresh: bool = False) -> Optional[str]:
    stale_token = None
    if not force_refresh:
        cached_token = get_cached_access_token(app_key, app_secret)
        if cached_token:
            return cached_token
        stale_token = load_token_cache().get("access_token")

    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body), timeout=20)
    if res.status_code == 200:
        payload = res.json()
        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 86400))
        if access_token:
            issued_at = datetime.now(timezone.utc)
            save_token_cache(
                {
                    "cache_key": get_token_cache_key(app_key, app_secret),
                    "access_token": access_token,
                    "issued_at": issued_at.isoformat(),
                    "expires_at": (issued_at + timedelta(seconds=expires_in)).isoformat(),
                }
            )
            return access_token
    if stale_token:
        return stale_token
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
            df = df[["stck_bsop_date", "stck_clpr", "frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn"]].copy()
            df.columns = ["Date", "Price", "Foreign_Amt", "Inst_Amt"]
            df["Date"] = pd.to_datetime(df["Date"])
            for col in ["Price", "Foreign_Amt", "Inst_Amt"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna()
            df["F_억"] = df["Foreign_Amt"] / 100
            df["I_억"] = df["Inst_Amt"] / 100
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


def scan_market(stock_dict: Dict[str, str], access_token: str, app_key: str, app_secret: str):
    filtered_map = {}
    summary = {"buy": 0, "mixed": 0, "sell": 0, "scanned": 0}
    direction_groups = {"buy": [], "mixed": [], "sell": []}

    for name, ticker in stock_dict.items():
        df = get_investor_data(ticker, access_token, app_key, app_secret)
        if df.empty or len(df) < 5:
            continue

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

    return filtered_map, summary, direction_groups


def build_scan_cache(app_key: str, app_secret: str):
    access_token = get_access_token(app_key, app_secret)
    if not access_token:
        raise RuntimeError("KIS API access token 발급에 실패했습니다.")

    dict_k200, dict_kq150, _ = get_stock_lists()
    generated_at = datetime.now(KST)
    target_date = get_target_date(generated_at)

    kospi_filtered, kospi_summary, kospi_groups = scan_market(dict_k200, access_token, app_key, app_secret)
    kosdaq_filtered, kosdaq_summary, kosdaq_groups = scan_market(dict_kq150, access_token, app_key, app_secret)

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
                "target_date": target_date,
            },
            "kosdaq150": {
                "label": "KOSDAQ 150",
                "market_size": len(dict_kq150),
                "symbols": dict_kq150,
                "filtered_map": kosdaq_filtered,
                "summary": kosdaq_summary,
                "direction_groups": kosdaq_groups,
                "target_date": target_date,
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
