
import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from datetime import datetime, timedelta, timezone
import streamlit.components.v1 as components
from scanner import classify_5day_direction, get_investor_data as fetch_investor_data
from scanner import get_access_token as fetch_access_token
from scanner import get_auto_refresh_window
from scanner import get_stock_lists as fetch_stock_lists
from scanner import get_target_date, load_scan_cache, save_scan_cache, summarize_5day_flow

# ==========================================
# 🔒 보안 설정 (Streamlit Secrets)
# ==========================================
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))

st.set_page_config(page_title="수급 쌍끌이 스캐너", layout="wide")
st.markdown(
    """
    <style>
    div[data-testid="stPlotlyChart"] {
        touch-action: pan-y pinch-zoom !important;
        -webkit-user-select: none;
        user-select: none;
    }
    div[data-testid="stPlotlyChart"] .js-plotly-plot,
    div[data-testid="stPlotlyChart"] .plot-container,
    div[data-testid="stPlotlyChart"] .plotly,
    div[data-testid="stPlotlyChart"] .svg-container {
        touch-action: pan-y pinch-zoom !important;
    }
    div[data-testid="stPlotlyChart"] .modebar {
        z-index: 1 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
components.html(
    """
    <script>
    (() => {
      const parentWindow = window.parent;
      const parentDoc = parentWindow.document;
      const isAndroid = /Android/i.test(parentWindow.navigator.userAgent || "");
      if (!isAndroid) return;

      const bindSingleTouchScroll = () => {
        parentDoc
          .querySelectorAll('div[data-testid="stPlotlyChart"]')
          .forEach((chart) => {
            if (chart.dataset.androidScrollBound === "1") return;
            chart.dataset.androidScrollBound = "1";

            const stopSingleTouchCapture = (event) => {
              if ((event.touches && event.touches.length === 1) || event.type === "touchend") {
                event.stopPropagation();
              }
            };

            ["touchstart", "touchmove", "touchend"].forEach((eventName) => {
              chart.addEventListener(eventName, stopSingleTouchCapture, {
                capture: true,
                passive: true,
              });
            });
          });
      };

      bindSingleTouchScroll();

      if (parentWindow.__plotlyAndroidTouchObserver) {
        parentWindow.__plotlyAndroidTouchObserver.disconnect();
      }

      parentWindow.__plotlyAndroidTouchObserver = new MutationObserver(bindSingleTouchScroll);
      parentWindow.__plotlyAndroidTouchObserver.observe(parentDoc.body, {
        childList: true,
        subtree: true,
      });
    })();
    </script>
    """,
    height=0,
)

# ==========================================
# 1. 데이터 수집 함수들
# ==========================================
@st.cache_data(ttl=86400)
def get_stock_lists():
    return fetch_stock_lists()

@st.cache_data(ttl=86000)
def get_access_token():
    return fetch_access_token(APP_KEY, APP_SECRET)

@st.cache_data(ttl=60, show_spinner=False)
def get_realtime_price(ticker, access_token):
    headers = {
        "content-type": "application/json; charset=utf-8", 
        "authorization": f"Bearer {access_token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET, 
        "tr_id": "FHKST01010100", "custtype": "P"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    url = f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
    
    try:
        res = requests.get(url, headers=headers, params=params)
        res_json = res.json()
        if res.status_code == 200 and 'output' in res_json:
            output = res_json['output']
            return {
                "price": int(output.get('stck_prpr', 0)),
                "diff": int(output.get('prdy_vrss', 0)),
                "rate": float(output.get('prdy_ctrt', 0.0))
            }
    except Exception:
        pass
    return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_investor_data(ticker, access_token):
    return fetch_investor_data(ticker, access_token, APP_KEY, APP_SECRET)


@st.cache_data(ttl=300, show_spinner=False)
def get_scan_cache():
    return load_scan_cache()


def persist_market_scan_cache(market_key, market_label, market_size, market_symbols, filtered_map, scan_summary, direction_groups):
    existing_cache = load_scan_cache()
    markets = dict(existing_cache.get("markets", {}))
    generated_at = datetime.now(KST).isoformat()
    target_date = get_target_date()

    markets[market_key] = {
        "label": market_label,
        "market_size": market_size,
        "symbols": market_symbols,
        "filtered_map": filtered_map,
        "summary": scan_summary,
        "direction_groups": direction_groups,
        "target_date": target_date,
    }

    save_scan_cache(
        {
            **existing_cache,
            "generated_at_kst": generated_at,
            "target_date": target_date,
            "markets": markets,
        }
    )
    get_scan_cache.clear()

def format_cache_timestamp(timestamp):
    if not timestamp:
        return "자동 갱신 정보 없음"
    try:
        parsed = datetime.fromisoformat(timestamp)
        return parsed.strftime("%Y-%m-%d %H:%M KST")
    except ValueError:
        return timestamp.replace("T", " ")[:16]

def format_target_date(date_str):
    if not date_str:
        return "-"
    try:
        return datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return date_str


def get_refresh_notice(cached_target_date, expected_target_date, generated_at, now=None):
    current = now.astimezone(KST) if now else datetime.now(KST)
    primary_time, backup_time = get_auto_refresh_window(current)

    if not generated_at:
        return (
            "warning",
            f"자동 갱신 캐시가 아직 없습니다. {primary_time.strftime('%H:%M')} 이후 새로 집계 또는 자동 갱신을 확인해 주세요.",
        )

    if cached_target_date == expected_target_date:
        return (
            "success",
            f"자동 갱신 {format_cache_timestamp(generated_at)} | 기준일 {format_target_date(cached_target_date)}",
        )

    if current.weekday() > 4:
        return (
            "info",
            f"현재 기준일은 {format_target_date(expected_target_date)} 이고, 마지막 자동 갱신은 {format_cache_timestamp(generated_at)} 입니다.",
        )

    if current < primary_time:
        return (
            "info",
            f"오늘 자동 갱신은 {primary_time.strftime('%H:%M')} 1차, {backup_time.strftime('%H:%M')} 2차로 예정되어 있습니다.",
        )

    if current < backup_time:
        return (
            "warning",
            f"1차 자동 갱신({primary_time.strftime('%H:%M')})이 아직 반영되지 않았습니다. "
            f"{backup_time.strftime('%H:%M')} 백업 실행을 기다리거나 수동 새로 집계를 눌러주세요.",
        )

    return (
        "error",
        f"오늘 자동 갱신이 아직 반영되지 않았습니다. "
        f"마지막 자동 갱신 {format_cache_timestamp(generated_at)} | 기준일 {format_target_date(cached_target_date)}",
    )

def build_direction_groups(target_dict, filtered_map, cached_groups=None):
    groups = {"buy": [], "mixed": [], "sell": []}

    if cached_groups:
        for direction, entries in cached_groups.items():
            if direction not in groups:
                continue
            for entry in entries:
                if isinstance(entry, str):
                    item = {"name": entry}
                else:
                    item = dict(entry)
                name = item.get("name")
                if name not in target_dict:
                    continue
                item["ticker"] = item.get("ticker") or target_dict.get(name)
                item["label"] = item.get("label") or filtered_map.get(name, name)
                groups[direction].append(item)

    known_names = {
        item["name"]
        for items in groups.values()
        for item in items
    }
    for name, label in filtered_map.items():
        if name in known_names:
            continue
        if "(↑↑)" in label:
            groups["buy"].append({"name": name, "label": label, "strength": 0})
        elif "(↓↓)" in label:
            groups["sell"].append({"name": name, "label": label, "strength": 0})

    for direction in groups:
        groups[direction].sort(key=lambda item: item.get("strength", 0), reverse=True)

    return groups


def get_cached_market_symbols(cached_market):
    symbols = cached_market.get("symbols", {})
    return symbols if isinstance(symbols, dict) else {}


def has_usable_cached_scan(cached_market, target_dict):
    summary = cached_market.get("summary", {})
    direction_groups = cached_market.get("direction_groups", {})
    cached_market_size = cached_market.get("market_size")

    if not summary or not direction_groups:
        return False

    if isinstance(cached_market_size, int) and cached_market_size > 1 and len(target_dict) > 1:
        if cached_market_size != len(target_dict):
            return False

    if len(target_dict) > 20 and summary.get("scanned", 0) <= 1:
        return False

    return True


def get_display_entries(direction_groups, display_filter):
    if display_filter == "buy":
        active_keys = ["buy"]
    elif display_filter == "sell":
        active_keys = ["sell"]
    else:
        active_keys = ["buy", "sell"]

    entries = []
    for key in active_keys:
        entries.extend(direction_groups.get(key, []))
    return entries

def scan_all_stocks(stock_dict, token):
    valid_stocks = {}
    summary = {"buy": 0, "mixed": 0, "sell": 0, "scanned": 0}
    direction_groups = {"buy": [], "mixed": [], "sell": []}
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(stock_dict)
    
    for i, (name, ticker) in enumerate(stock_dict.items()):
        status_text.text(f"🚀 스캔 중 ({i+1}/{total}): {name}")
        df = get_investor_data(ticker, token)
        if not df.empty and len(df) >= 5:
            direction = classify_5day_direction(df)
            flow = summarize_5day_flow(df)
            summary["scanned"] += 1
            summary[direction] += 1
            label = name
            if direction == "buy":
                label = f"{name} (↑↑)"
                valid_stocks[name] = label
            elif direction == "sell":
                label = f"{name} (↓↓)"
                valid_stocks[name] = label
            direction_groups[direction].append(
                {
                    "name": name,
