from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import requests

URL_BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))
CACHE_FILE = Path(__file__).parent / "data" / "scan_cache.json"


def get_target_date(now: datetime | None = None) -> str:
    current = now.astimezone(KST) if now else datetime.now(KST)
    if current.hour < 15 or (current.hour == 15 and current.minute < 40):
        target = current - timedelta(days=1)
    else:
        target = current
    while target.weekday() > 4:
        target -= timedelta(days=1)
    return target.strftime("%Y%m%d")


def get_stock_lists():
    try:
        df_kospi = fdr.StockListing("KOSPI")
        df_kosdaq = fdr.StockListing("KOSDAQ")
        df_all = fdr.StockListing("KRX")

        mcap_col = "Marcap" if "Marcap" in df_kospi.columns else "MarCap"

        k200 = df_kospi.sort_values(mcap_col, ascending=False).head(200)
        kq150 = df_kosdaq.sort_values(mcap_col, ascending=False).head(150)

        dict_k200 = dict(zip(k200["Name"], k200["Code"]))
        dict_kq150 = dict(zip(kq150["Name"], kq150["Code"]))
        dict_all = dict(zip(df_all["Name"], df_all["Code"]))

        return dict_k200, dict_kq150, dict_all
    except Exception:
        return {"삼성전자": "005930"}, {"에코프로": "086520"}, {"삼성전자": "005930"}


def get_access_token(app_key: str, app_secret: str) -> str | None:
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body), timeout=20)
    if res.status_code == 200:
        return res.json().get("access_token")
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


def scan_market(stock_dict: dict[str, str], access_token: str, app_key: str, app_secret: str):
    filtered_map = {}
    summary = {"buy": 0, "mixed": 0, "sell": 0, "scanned": 0}

    for name, ticker in stock_dict.items():
        df = get_investor_data(ticker, access_token, app_key, app_secret)
        if df.empty or len(df) < 5:
            continue

        direction = classify_5day_direction(df)
        summary["scanned"] += 1
        summary[direction] += 1

        if direction == "buy":
            filtered_map[name] = f"{name} (↑↑)"
        elif direction == "sell":
            filtered_map[name] = f"{name} (↓↓)"

    return filtered_map, summary


def build_scan_cache(app_key: str, app_secret: str):
    access_token = get_access_token(app_key, app_secret)
    if not access_token:
        raise RuntimeError("KIS API access token 발급에 실패했습니다.")

    dict_k200, dict_kq150, _ = get_stock_lists()
    generated_at = datetime.now(KST)

    kospi_filtered, kospi_summary = scan_market(dict_k200, access_token, app_key, app_secret)
    kosdaq_filtered, kosdaq_summary = scan_market(dict_kq150, access_token, app_key, app_secret)

    return {
        "generated_at_kst": generated_at.isoformat(),
        "target_date": get_target_date(generated_at),
        "markets": {
            "kospi200": {
                "label": "KOSPI 200",
                "market_size": len(dict_k200),
                "filtered_map": kospi_filtered,
                "summary": kospi_summary,
            },
            "kosdaq150": {
                "label": "KOSDAQ 150",
                "market_size": len(dict_kq150),
                "filtered_map": kosdaq_filtered,
                "summary": kosdaq_summary,
            },
        },
    }


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
