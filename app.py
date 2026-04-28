import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import inspect
from datetime import datetime, timedelta, timezone
import streamlit.components.v1 as components
from scanner import classify_5day_direction, get_investor_data as fetch_investor_data
from scanner import get_access_token as fetch_access_token
from scanner import attach_previous_market_snapshots
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
DATAFRAME_SUPPORTS_SELECTION = "on_select" in inspect.signature(st.dataframe).parameters
THEME_BASE = st.get_option("theme.base") or "light"


def get_new_entry_highlight_style():
    if THEME_BASE == "dark":
        return "background-color: #0f3d5e; color: #f3f8fc; font-weight: 700;"
    return "background-color: #fff3bf; color: #1f2328; font-weight: 700;"


def get_mixed_transition_highlight_styles():
    if THEME_BASE == "dark":
        return {
            "from_buy": "background-color: #15543c; color: #f2fbf5; font-weight: 700;",
            "from_sell": "background-color: #6a2436; color: #fff4f6; font-weight: 700;",
            "new": get_new_entry_highlight_style(),
        }
    return {
        "from_buy": "background-color: #d7f5df; color: #184d2d; font-weight: 700;",
        "from_sell": "background-color: #f8d7df; color: #6f1d2d; font-weight: 700;",
        "new": get_new_entry_highlight_style(),
    }

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

    payload = {
        **existing_cache,
        "generated_at_kst": generated_at,
        "target_date": target_date,
        "markets": markets,
    }
    save_scan_cache(attach_previous_market_snapshots(existing_cache, payload))
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


def get_previous_direction_names(cached_market, direction):
    groups = cached_market.get("previous_direction_groups", {}) if isinstance(cached_market, dict) else {}
    names = set()
    for item in groups.get(direction, []):
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and item.get("name"):
            names.add(item["name"])
    return names
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
                    "ticker": ticker,
                    "label": label,
                    **flow,
                }
            )
                
        progress_bar.progress((i + 1) / total)
        # ⚡ 0.05초로 복구
        time.sleep(0.05)
        
    status_text.empty()
    progress_bar.empty()
    for direction in direction_groups:
        direction_groups[direction].sort(key=lambda item: item.get("strength", 0), reverse=True)
    return valid_stocks, summary, direction_groups

# ==========================================
# 3. 메인 화면: 탭 및 컨트롤러 구성
# ==========================================
# 🎨 폰트 사이즈 조정 (H2 태그 적용)
st.markdown("<h2 style='margin-bottom: 20px;'>📊 쌍끌이 수급 스캐너</h2>", unsafe_allow_html=True)

# 3개 리스트 다시 받아옴
dict_k200, dict_kq150, dict_all = get_stock_lists() 
token = get_access_token()

# 🎯 3개 탭 유지
market_mode = st.radio(
    "분석 시장 선택", 
    ["🔵 KOSPI 200", "🟢 KOSDAQ 150", "🔍 전체 종목 (개별 검색)"], 
    horizontal=True
)

if 'current_market' not in st.session_state:
    st.session_state.current_market = market_mode

if st.session_state.current_market != market_mode:
    st.session_state.current_idx = 0
    st.session_state.current_market = market_mode
    if 'filtered_map' in st.session_state:
        del st.session_state.filtered_map
    if 'scan_summary' in st.session_state:
        del st.session_state.scan_summary
    if 'scan_direction_groups' in st.session_state:
        del st.session_state.scan_direction_groups
    st.session_state.scan_display_filter = "all"
    st.session_state.scan_focus = None

if 'current_idx' not in st.session_state:
    st.session_state.current_idx = 0
if 'scan_display_filter' not in st.session_state:
    st.session_state.scan_display_filter = "all"
if 'scan_focus' not in st.session_state:
    st.session_state.scan_focus = None
if 'pending_selected_disp' not in st.session_state:
    st.session_state.pending_selected_disp = None

# 🎯 탭에 따른 로직 분리 (전체 종목 탭은 스캔 불가 처리)
if market_mode == "🔵 KOSPI 200":
    target_dict = dict_k200
    allow_scan = True
    market_cache_key = "kospi200"
    market_cache_label = "KOSPI 200"
elif market_mode == "🟢 KOSDAQ 150":
    target_dict = dict_kq150
    allow_scan = True
    market_cache_key = "kosdaq150"
    market_cache_label = "KOSDAQ 150"
else:
    target_dict = dict_all
    allow_scan = False
    market_cache_key = None
    market_cache_label = None

scan_cache = get_scan_cache()
cached_market = scan_cache.get("markets", {}).get(market_cache_key, {}) if market_cache_key else {}
cached_generated_at = scan_cache.get("generated_at_kst")
current_target_date = get_target_date()

if allow_scan:
    cached_symbols = get_cached_market_symbols(cached_market)
    if cached_symbols and len(target_dict) <= 1:
        target_dict = cached_symbols

    if cached_market and not has_usable_cached_scan(cached_market, target_dict):
        cached_market = {}

h_col1, h_col2, h_col3 = st.columns([1, 1.5, 1.2])

with h_col1:
    # 코스피/코스닥일 때만 스캐너 필터 활성화
    if allow_scan:
        filter_col, summary_col = st.columns([1.05, 1.95])
        with filter_col:
            is_filtered = st.checkbox("🔥 5일 동방향 필터")
        with summary_col:
            summary_placeholder = st.empty()
    else:
        is_filtered = False
        st.caption("✅ 전체 종목은 스캔이 제한되며 개별 검색만 가능합니다.")

with h_col2:
    period = st.select_slider("분석 기간", options=[5, 10, 15, 20, 25, 30], value=30, label_visibility="collapsed")

if is_filtered and allow_scan:
    if (
        'filtered_map' not in st.session_state
        or 'scan_summary' not in st.session_state
        or 'scan_direction_groups' not in st.session_state
    ):
        if cached_market.get("summary"):
            st.session_state.filtered_map = cached_market.get("filtered_map", {})
            st.session_state.scan_summary = cached_market["summary"]
            st.session_state.scan_direction_groups = build_direction_groups(
                target_dict,
                st.session_state.filtered_map,
                cached_market.get("direction_groups"),
            )
        elif token:
            filtered_map, scan_summary, direction_groups = scan_all_stocks(target_dict, token)
            st.session_state.filtered_map = filtered_map
            st.session_state.scan_summary = scan_summary
            st.session_state.scan_direction_groups = build_direction_groups(
                target_dict,
                filtered_map,
                direction_groups,
            )
            persist_market_scan_cache(
                market_cache_key,
                market_cache_label,
                len(target_dict),
                target_dict,
                filtered_map,
                scan_summary,
                direction_groups,
            )
            scan_cache = get_scan_cache()
            cached_market = scan_cache.get("markets", {}).get(market_cache_key, {})
            cached_generated_at = scan_cache.get("generated_at_kst")
        else:
            st.error("API 토큰 발급 실패")
            st.stop()
    if market_mode in ("🔵 KOSPI 200", "🟢 KOSDAQ 150"):
        scan_summary = st.session_state.scan_summary
        direction_groups = st.session_state.scan_direction_groups
        cached_target_date = cached_market.get("target_date") or scan_cache.get("target_date")
        target_date = cached_target_date or current_target_date
        notice_level, notice_message = get_refresh_notice(
            cached_target_date,
            current_target_date,
            cached_generated_at,
        )
        focus_meta = {
            "buy": ("쌍끌이매수", "secondary"),
            "mixed": ("엇갈림", "secondary"),
            "sell": ("쌍끌이매도", "secondary"),
        }
        with summary_placeholder.container():
            if notice_level == "success":
                st.caption(notice_message)
            elif notice_level == "info":
                st.info(notice_message)
            elif notice_level == "warning":
                st.warning(notice_message)
            else:
                st.error(notice_message)
            st.caption(f"집계 {scan_summary['scanned']}/{len(target_dict)} | 기준일 {format_target_date(target_date)}")
            refresh_disabled = not bool(token)
            refresh_help = "KIS 토큰이 없어서 지금은 새로 집계할 수 없습니다." if refresh_disabled else "실시간으로 다시 스캔해서 목록을 갱신합니다."
            controls_col = st.columns([1])[0]
            with controls_col:
                if st.button(
                    "새로 집계",
                    key=f"{market_cache_key}_refresh_scan",
                    width="stretch",
                    disabled=refresh_disabled,
                    help=refresh_help,
                ):
                    filtered_map, scan_summary, direction_groups = scan_all_stocks(target_dict, token)
                    st.session_state.filtered_map = filtered_map
                    st.session_state.scan_summary = scan_summary
                    st.session_state.scan_direction_groups = build_direction_groups(
                        target_dict,
                        filtered_map,
                        direction_groups,
                    )
                    persist_market_scan_cache(
                        market_cache_key,
                        market_cache_label,
                        len(target_dict),
                        target_dict,
                        filtered_map,
                        scan_summary,
                        direction_groups,
                    )
                    scan_cache = get_scan_cache()
                    cached_market = scan_cache.get("markets", {}).get(market_cache_key, {})
                    cached_generated_at = scan_cache.get("generated_at_kst")
                    target_date = current_target_date
                    direction_groups = st.session_state.scan_direction_groups

            buy_col, mixed_col, sell_col = st.columns(3)
            for direction, column in zip(
                ["buy", "mixed", "sell"],
                [buy_col, mixed_col, sell_col],
            ):
                count = scan_summary[direction]
                label, default_type = focus_meta[direction]
                short_label = {
                    "buy": "매수",
                    "mixed": "엇갈림",
                    "sell": "매도",
                }[direction]
                button_type = "primary" if st.session_state.scan_focus == direction else default_type
                if column.button(
                    f"{short_label} {count}",
                    key=f"{market_cache_key}_{direction}_summary",
                    width="stretch",
                    type=button_type,
                    help=f"{label} 종목 보기",
                ):
                    if st.session_state.scan_focus == direction:
                        st.session_state.scan_focus = None
                        st.session_state.scan_display_filter = "all"
                    else:
                        st.session_state.scan_focus = direction
                        st.session_state.scan_display_filter = direction if direction in ("buy", "sell") else "all"

            focus = st.session_state.scan_focus
            if focus:
                focus_items = direction_groups.get(focus, [])
                focus_title = focus_meta[focus][0]
                previous_target_date = cached_market.get("previous_target_date")
                previous_direction_names = get_previous_direction_names(cached_market, focus)
                previous_buy_names = get_previous_direction_names(cached_market, "buy")
                previous_sell_names = get_previous_direction_names(cached_market, "sell")
                new_entry_names = {
                    item["name"]
                    for item in focus_items
                    if item["name"] not in previous_direction_names
                }
                entry_styles = {}
                mixed_from_buy_names = set()
                mixed_from_sell_names = set()
                other_new_names = set()
                if focus == "mixed":
                    mixed_transition_styles = get_mixed_transition_highlight_styles()
                    for name in new_entry_names:
                        if name in previous_buy_names:
                            entry_styles[name] = mixed_transition_styles["from_buy"]
                            mixed_from_buy_names.add(name)
                        elif name in previous_sell_names:
                            entry_styles[name] = mixed_transition_styles["from_sell"]
                            mixed_from_sell_names.add(name)
                        else:
                            entry_styles[name] = mixed_transition_styles["new"]
                            other_new_names.add(name)
                else:
                    default_style = get_new_entry_highlight_style()
                    entry_styles = {name: default_style for name in new_entry_names}
                with st.expander(f"{focus_title} 종목 {len(focus_items)}개", expanded=True):
                    if focus_items:
                        focus_df = pd.DataFrame(
                            {
                                "종목": [item["name"] for item in focus_items],
                                "외인 5일합": [item.get("foreign_5d", "-") for item in focus_items],
                                "기관 5일합": [item.get("inst_5d", "-") for item in focus_items],
                                "합계": [item.get("total_5d", "-") for item in focus_items],
                            }
                        )
                        styled_focus_df = focus_df.style.apply(
                            lambda col: [
                                entry_styles.get(name, "")
                                for name in col
                            ],
                            subset=["종목"],
                        )
                        if DATAFRAME_SUPPORTS_SELECTION:
                            selection = st.dataframe(
                                styled_focus_df,
                                width="stretch",
                                hide_index=True,
                                on_select="rerun",
                                selection_mode=["single-row", "single-cell"],
                                key=f"{market_cache_key}_{focus}_focus_table",
                            )
                            selected_rows = list(getattr(selection.selection, "rows", []))
                            selected_cells = list(getattr(selection.selection, "cells", []))
                            if not selected_rows and selected_cells:
                                first_cell = selected_cells[0]
                                if isinstance(first_cell, (list, tuple)) and first_cell:
                                    selected_rows = [first_cell[0]]
                                elif isinstance(first_cell, dict) and "row" in first_cell:
                                    selected_rows = [first_cell["row"]]
                            if selected_rows:
                                selected_item = focus_items[selected_rows[0]]
                                st.session_state.pending_selected_disp = selected_item.get(
                                    "label",
                                    selected_item["name"],
                                )
                        else:
                            st.dataframe(styled_focus_df, width="stretch", hide_index=True)
                            st.caption("현재 환경에서는 표 선택 이동을 지원하지 않습니다.")
                        if previous_target_date:
                            new_count = len(new_entry_names)
                            if focus == "mixed":
                                st.caption(
                                    f"{format_target_date(previous_target_date)} 대비 오늘 새 진입 {new_count}개 | "
                                    f"녹색 계열 = 매수→엇갈림 {len(mixed_from_buy_names)}개 | "
                                    f"붉은 계열 = 매도→엇갈림 {len(mixed_from_sell_names)}개 | "
                                    f"기본 강조 = 기타 신규 {len(other_new_names)}개"
                                )
                            else:
                                st.caption(
                                    f"노란 배경 = {format_target_date(previous_target_date)} 대비 오늘 새 진입 {new_count}개"
                                )
                    else:
                        st.caption("현재 조건에 맞는 종목이 없습니다.")
                    if st.button("목록 닫기", key=f"{market_cache_key}_{focus}_close", width="stretch"):
                        st.session_state.scan_focus = None
                        if focus == "mixed":
                            st.session_state.scan_display_filter = "all"

        display_entries = get_display_entries(direction_groups, st.session_state.scan_display_filter)
    else:
        display_entries = [
            {"name": name, "label": label, "ticker": target_dict.get(name)}
            for name, label in st.session_state.filtered_map.items()
        ]

    display_names = [item["label"] for item in display_entries]
    name_lookup = {item["label"]: item["name"] for item in display_entries}
    ticker_lookup = {item["label"]: item.get("ticker") for item in display_entries}
else:
    display_names = list(target_dict.keys())
    name_lookup = {n: n for n in display_names}
    ticker_lookup = {n: target_dict.get(n) for n in display_names}
    if allow_scan:
        summary_placeholder.empty()

if not display_names:
    st.warning("조건에 맞는 종목이 없습니다.")
    display_names = ["삼성전자"]; name_lookup = {"삼성전자": "삼성전자"}
    ticker_lookup = {"삼성전자": "005930"}

pending_selected_disp = st.session_state.get("pending_selected_disp")
if pending_selected_disp in display_names:
    st.session_state.current_idx = display_names.index(pending_selected_disp)
    st.session_state.stock_selector = pending_selected_disp
    st.session_state.pending_selected_disp = None

def go_prev():
    if st.session_state.current_idx > 0:
        st.session_state.current_idx -= 1
        st.session_state.stock_selector = display_names[st.session_state.current_idx]
def go_next():
    if st.session_state.current_idx < len(display_names) - 1:
        st.session_state.current_idx += 1
        st.session_state.stock_selector = display_names[st.session_state.current_idx]
def on_change():
    if 'stock_selector' in st.session_state and st.session_state.stock_selector in display_names:
        st.session_state.current_idx = display_names.index(st.session_state.stock_selector)

c1, c2, c3 = st.columns([1, 2, 1])
with c1: st.button("⬅️ 이전", on_click=go_prev, width="stretch")
with c2:
    if st.session_state.current_idx >= len(display_names):
        st.session_state.current_idx = 0
    if st.session_state.get("stock_selector") not in display_names:
        st.session_state.current_idx = 0
    selected_disp = st.selectbox("종목 선택", display_names, index=st.session_state.current_idx, 
                                 key="stock_selector", on_change=on_change, label_visibility="collapsed")
with c3: st.button("다음 ➡️", on_click=go_next, width="stretch")

selected_real = name_lookup.get(selected_disp, selected_disp)
selected_ticker = ticker_lookup.get(selected_disp) or target_dict.get(selected_real, "005930")

# ==========================================
# 4. 차트 및 표 시각화
# ==========================================
if token:
    df = get_investor_data(selected_ticker, token)
    rt_data = get_realtime_price(selected_ticker, token)
    
    if not df.empty:
        df_disp = df.tail(period).copy()
        
        if rt_data:
            curr_p, diff, ratio = rt_data['price'], rt_data['diff'], rt_data['rate']
        else:
            curr_p = df_disp['Price'].iloc[-1]
            prev_p = df_disp['Price'].iloc[-2] if len(df_disp) > 1 else curr_p
            diff = curr_p - prev_p
            ratio = (diff / prev_p) * 100 if prev_p != 0 else 0
        
        direction = classify_5day_direction(df_disp)
        if direction == "buy":
            b_html = '<span style="background-color:#ff4b4b;color:white;padding:2px 6px;border-radius:4px;font-size:0.8rem;">쌍끌이 매수 ↑↑</span>'
        elif direction == "sell":
            b_html = '<span style="background-color:#31333f;color:white;padding:2px 6px;border-radius:4px;font-size:0.8rem;">쌍끌이 매도 ↓↓</span>'
        else: 
            b_html = '<span style="background-color:#f0f2f6;color:#31333f;padding:2px 6px;border-radius:4px;font-size:0.8rem;">엇갈림</span>'

        with h_col3:
            p_c = "red" if diff > 0 else "blue" if diff < 0 else "gray"
            st.markdown(f'<div style="text-align:right;line-height:1.4;"><div>{b_html}</div><div style="font-size:1.05rem;font-weight:bold;">{curr_p:,.0f} <span style="color:{p_c};font-size:0.9rem;">({"▲" if diff>0 else "▼" if diff<0 else ""}{abs(diff):,.0f}, {ratio:.2f}%)</span></div></div>', unsafe_allow_html=True)

        df_disp['F_누적'] = df_disp['F_억'].cumsum()
        df_disp['I_누적'] = df_disp['I_억'].cumsum()

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=df_disp.index, y=df_disp['F_누적'], name='외인누적(억)', line=dict(color='blue', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df_disp.index, y=df_disp['I_누적'], name='기관누적(억)', line=dict(color='orange', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df_disp.index, y=df_disp['Price'], name='주가', line=dict(color='red', width=1.5, dash='dot')), secondary_y=True)
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.update_layout(
            title=f"<b>{selected_real}</b>", hovermode="x unified", height=450, 
            margin=dict(l=5,r=5,t=50,b=5),
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor='center'),
            dragmode=False,
        )
        st.plotly_chart(
            fig,
            width="stretch",
            config={
                "scrollZoom": True,
                "doubleClick": False,
                "displaylogo": False,
                "modeBarButtonsToRemove": [
                    "zoom2d",
                    "pan2d",
                    "select2d",
                    "lasso2d",
                    "zoomIn2d",
                    "zoomOut2d",
                    "autoScale2d",
                ],
            },
        )

        st.write("##### 📋 상세 내역 (단위: 억원)")
        res_df = df_disp[['Price','F_억','I_억','F_누적','I_누적']].iloc[::-1].copy()
        res_df.columns = ['주가','외인_일일','기관_일일','외인_누적','기관_누적']

        res_df.index = res_df.index.strftime('%Y-%m-%d')
        
        def color_net_buy(val):
            try:
                v = float(val)
                if v > 0: return 'color: #ff4b4b; font-weight: bold;'
                elif v < 0: return 'color: #1f77b4;'
            except: pass
            return ''
            
        try:
            styled_df = res_df.style.format("{:,.1f}").map(color_net_buy, subset=['외인_일일', '기관_일일', '외인_누적', '기관_누적'])
        except AttributeError:
            styled_df = res_df.style.format("{:,.1f}").applymap(color_net_buy, subset=['외인_일일', '기관_일일', '외인_누적', '기관_누적'])
            
        st.dataframe(styled_df, width="stretch")

        info_parts = [
            f"수급 기준일 {format_target_date(cached_market.get('target_date') if is_filtered and allow_scan else get_target_date())}",
            "당일 수급 데이터는 보통 16:30 이후 반영",
        ]
        if is_filtered and allow_scan:
            info_parts.append(f"자동 갱신 {format_cache_timestamp(cached_generated_at)}")
        st.caption(" | ".join(info_parts))
    else:
        st.error("데이터를 불러올 수 없습니다. 아래 API 로그를 확인해 주세요.")

# ==========================================
# 🚨 API 디버그 로그
# ==========================================
st.markdown("---")
with st.expander("🛠️ 시스템 로그 보기 (에러 원인 파악용)"):
    if token:
        st.write(f"현재 선택된 종목: **{selected_real}** (코드: {selected_ticker})")
        st.write(f"수급 요청 기준일자(KST): **{get_target_date()}**")
        headers = {
            "content-type": "application/json; charset=utf-8", 
            "authorization": f"Bearer {token}",
            "appkey": APP_KEY, "appsecret": APP_SECRET, 
            "tr_id": "FHPTJ04160001", "custtype": "P"
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": selected_ticker,
            "FID_INPUT_DATE_1": get_target_date(), "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": "1"
        }
        url = f"{URL_BASE}/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
        try:
            raw_res = requests.get(url, headers=headers, params=params)
            st.write(f"**HTTP 상태 코드:** {raw_res.status_code}")
            try:
                st.json(raw_res.json())
            except:
                st.text("JSON 변환 실패. 원본 텍스트:")
                st.text(raw_res.text)
        except Exception as e:
            st.error(f"서버 연결 실패: {str(e)}")
