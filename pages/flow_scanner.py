from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from market_data import cache_file_version
from scanner import (
    CACHE_FILE,
    INVESTOR_CHART_MAX_ROWS,
    cache_has_target_date,
    classify_5day_direction,
    get_stock_lists,
    get_target_date,
    load_scan_cache,
)
from github_actions import (
    WORKFLOW_URL,
    WorkflowDispatchError,
    dispatch_market_cache_workflow,
)


KST = timezone(timedelta(hours=9))
STOCK_SELECTOR_KEY = "flow_stock_selector"
MANUAL_REFRESH_REQUEST_KEY = "manual_market_refresh_request"
MANUAL_REFRESH_COOLDOWN = timedelta(minutes=65)
BATCH_STATE_FILE = Path(__file__).resolve().parents[1] / "data" / "kis_batch_state.json"
STOCK_KEYBOARD_NAVIGATION = st.components.v2.component(
    "flow_stock_keyboard_navigation",
    css=":host { display: none; }",
    js="""
    export default function() {
      const clickButtonByText = (needle) => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const button = buttons.find((element) =>
          (element.innerText || "").includes(needle)
        );
        if (button && !button.disabled) {
          button.click();
        }
      };

      const isEditingTarget = (target) => {
        if (!target || !target.closest) return false;
        return Boolean(
          target.closest("input, textarea, select, [contenteditable='true']") ||
          target.closest("[role='combobox'], [role='listbox'], [role='option']")
        );
      };

      const onKeyDown = (event) => {
        if (isEditingTarget(event.target)) return;

        if (event.key === "ArrowLeft") {
          event.preventDefault();
          event.stopPropagation();
          clickButtonByText("이전 종목");
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          event.stopPropagation();
          clickButtonByText("다음 종목");
        }
      };

      document.addEventListener("keydown", onKeyDown, true);
      return () => document.removeEventListener("keydown", onKeyDown, true);
    }
    """,
)
DIRECTION_META = {
    "buy": ("쌍끌이 매수", "↑↑", "#ef4444"),
    "mixed": ("엇갈림", "↕", "#64748b"),
    "sell": ("쌍끌이 매도", "↓↓", "#2563eb"),
}


@st.cache_data(max_entries=2, show_spinner=False)
def get_scan_cache(cache_version: tuple[int, int]) -> dict:
    del cache_version
    return load_scan_cache()


@st.cache_data(max_entries=2, show_spinner=False)
def get_batch_state(cache_version: tuple[int, int]) -> dict:
    del cache_version
    try:
        payload = json.loads(BATCH_STATE_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@st.cache_data(ttl=86400, show_spinner=False)
def get_all_symbols() -> dict[str, str]:
    _, _, symbols = get_stock_lists()
    return symbols


def format_target_date(value: str | None) -> str:
    try:
        return datetime.strptime(value or "", "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return value or "-"


def format_generated_at(value: str | None) -> str:
    try:
        parsed = datetime.fromisoformat(value or "")
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except ValueError:
        return (value or "-").replace("T", " ")[:16]


def get_actions_token() -> str:
    token = os.environ.get("GITHUB_ACTIONS_TOKEN", "").strip()
    if token:
        return token
    try:
        return str(st.secrets.get("GITHUB_ACTIONS_TOKEN", "")).strip()
    except Exception:
        return ""


def batch_is_complete_for_target(
    scan_cache: dict,
    batch_state: dict,
    target_date: str,
) -> bool:
    batch = batch_state.get("batch", {})
    return (
        batch.get("target_date") == target_date
        and batch.get("status") == "success"
        and cache_has_target_date(scan_cache, target_date)
    )


def get_pending_manual_request(target_date: str) -> dict | None:
    request = st.session_state.get(MANUAL_REFRESH_REQUEST_KEY)
    if not isinstance(request, dict) or request.get("target_date") != target_date:
        return None
    try:
        requested_at = datetime.fromisoformat(str(request["requested_at_kst"]))
    except (KeyError, TypeError, ValueError):
        return None
    if datetime.now(KST) - requested_at.astimezone(KST) >= MANUAL_REFRESH_COOLDOWN:
        return None
    return request


def render_manual_refresh(scan_cache: dict, batch_state: dict) -> None:
    target_date = get_target_date()
    completed = batch_is_complete_for_target(scan_cache, batch_state, target_date)
    pending_request = get_pending_manual_request(target_date)
    actions_token = get_actions_token()

    status_column, button_column = st.columns([4, 1])
    with status_column:
        if completed:
            st.caption(
                f"{format_target_date(target_date)} 배치 완료 · 같은 거래일은 중복 실행하지 않습니다."
            )
        elif pending_request:
            st.info("수동 갱신을 요청했습니다. 완료까지 최대 60분 정도 걸릴 수 있습니다.")
        else:
            st.caption(
                f"갱신 대상 {format_target_date(target_date)} · 자동 배치가 누락됐을 때 수동으로 실행합니다."
            )

    with button_column:
        if not completed and pending_request is None and not actions_token:
            st.link_button(
                "🔄 수동 갱신",
                WORKFLOW_URL,
                width="stretch",
                type="primary",
                help="GitHub 로그인 후 Run workflow를 눌러 실행합니다.",
            )
        elif st.button(
            "🔄 수동 갱신",
            key="manual_market_refresh",
            width="stretch",
            type="primary",
            disabled=completed or pending_request is not None,
            help=(
                "이미 완료된 거래일이라 중복 실행하지 않습니다."
                if completed
                else "GitHub Actions 장 마감 배치를 요청합니다."
            ),
        ):
            try:
                result = dispatch_market_cache_workflow(actions_token)
            except WorkflowDispatchError as exc:
                st.error(str(exc))
                st.link_button("GitHub Actions 확인", WORKFLOW_URL, width="stretch")
                return
            st.session_state[MANUAL_REFRESH_REQUEST_KEY] = {
                "target_date": target_date,
                "requested_at_kst": datetime.now(KST).isoformat(),
                "run_url": result.run_url,
            }
            st.rerun()

    if pending_request and pending_request.get("run_url"):
        st.link_button(
            "수동 갱신 진행상황 보기",
            str(pending_request["run_url"]),
            width="stretch",
        )


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    required = ["Date", "Price", "F_억", "I_억", "P_억"]
    for column in required:
        if column not in frame:
            frame[column] = 0
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["Date", "Price"]).sort_values("Date").set_index("Date")


def find_chart_frame(scan_cache: dict, ticker: str) -> pd.DataFrame:
    for market in scan_cache.get("markets", {}).values():
        chart_rows = market.get("chart_data", {}).get(ticker)
        if chart_rows:
            return rows_to_frame(chart_rows)
    return pd.DataFrame()


def select_relative_stock(options: list[str], step: int) -> None:
    if not options:
        return
    current = st.session_state.get(STOCK_SELECTOR_KEY)
    current_index = options.index(current) if current in options else 0
    target_index = max(0, min(len(options) - 1, current_index + step))
    st.session_state[STOCK_SELECTOR_KEY] = options[target_index]


def render_stock_navigation(options: list[str]) -> str:
    current = st.session_state.get(STOCK_SELECTOR_KEY)
    if current not in options:
        st.session_state[STOCK_SELECTOR_KEY] = options[0]
        current = options[0]
    current_index = options.index(current)

    previous_column, selector_column, next_column = st.columns([1, 3, 1])
    with previous_column:
        st.button(
            "⬅️ 이전 종목",
            key="flow_previous_stock",
            disabled=current_index == 0,
            on_click=select_relative_stock,
            args=(options, -1),
            width="stretch",
        )
    with selector_column:
        selected = st.selectbox(
            "종목 선택",
            options,
            key=STOCK_SELECTOR_KEY,
            label_visibility="collapsed",
        )
    with next_column:
        st.button(
            "다음 종목 ➡️",
            key="flow_next_stock",
            disabled=current_index == len(options) - 1,
            on_click=select_relative_stock,
            args=(options, 1),
            width="stretch",
        )

    STOCK_KEYBOARD_NAVIGATION(key="flow_stock_keyboard_navigation")
    st.caption("키보드 ← / → 키로 이전·다음 종목을 연속해서 볼 수 있습니다.")
    return selected


def normalized_groups(market: dict) -> dict[str, list[dict]]:
    groups = {"buy": [], "mixed": [], "sell": []}
    symbols = market.get("symbols", {})
    for direction in groups:
        for value in market.get("direction_groups", {}).get(direction, []):
            item = {"name": value} if isinstance(value, str) else dict(value)
            if not item.get("name"):
                continue
            item["ticker"] = item.get("ticker") or symbols.get(item["name"])
            item["direction"] = direction
            item["label"] = item.get("label") or item["name"]
            groups[direction].append(item)
    return groups


def render_status(scan_cache: dict, market: dict) -> None:
    cached_date = market.get("target_date") or scan_cache.get("target_date")
    expected_date = get_target_date()
    generated_at = market.get("generated_at_kst") or scan_cache.get("generated_at_kst")
    message = (
        f"배치 갱신 {format_generated_at(generated_at)} · "
        f"수급 기준일 {format_target_date(cached_date)}"
    )
    if cached_date == expected_date:
        st.success(message)
    else:
        st.warning(
            f"{message} · 현재 예상 기준일 {format_target_date(expected_date)}. "
            "다음 장 마감 배치에서 갱신됩니다."
        )


def render_summary(summary: dict) -> None:
    columns = st.columns(4)
    columns[0].metric("분석 완료", f"{summary.get('scanned', 0):,}종목")
    columns[1].metric("쌍끌이 매수", f"{summary.get('buy', 0):,}")
    columns[2].metric("엇갈림", f"{summary.get('mixed', 0):,}")
    columns[3].metric("쌍끌이 매도", f"{summary.get('sell', 0):,}")


def render_flow_table(entries: list[dict], previous_groups: dict) -> None:
    if not entries:
        st.info("선택한 방향에 해당하는 종목이 없습니다.")
        return
    previous_direction = {}
    for direction, values in previous_groups.items():
        for value in values:
            name = value if isinstance(value, str) else value.get("name")
            if name:
                previous_direction[name] = direction

    rows = []
    for item in entries:
        previous = previous_direction.get(item["name"])
        rows.append(
            {
                "종목": item["name"],
                "상태": DIRECTION_META[item["direction"]][0],
                "외인 5일합": item.get("foreign_5d", 0),
                "기관 5일합": item.get("inst_5d", 0),
                "합계": item.get("total_5d", 0),
                "전일 대비": (
                    "신규"
                    if previous is None
                    else f"{DIRECTION_META[previous][0]} → {DIRECTION_META[item['direction']][0]}"
                    if previous != item["direction"]
                    else "유지"
                ),
            }
        )
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame.style.format(
            {"외인 5일합": "{:,.1f}", "기관 5일합": "{:,.1f}", "합계": "{:,.1f}"}
        ),
        hide_index=True,
        width="stretch",
        height=min(560, 38 * (len(frame) + 1)),
    )


def render_stock_chart(name: str, ticker: str, frame: pd.DataFrame, period: int) -> None:
    display = frame.tail(period).copy()
    display["외인 누적"] = display["F_억"].cumsum()
    display["기관 누적"] = display["I_억"].cumsum()
    display["개인 누적"] = display["P_억"].cumsum()
    direction = classify_5day_direction(display)
    title, arrow, color = DIRECTION_META[direction]
    current = display["Price"].iloc[-1]
    previous = display["Price"].iloc[-2] if len(display) > 1 else current
    difference = current - previous
    rate = difference / previous * 100 if previous else 0

    left, right = st.columns([3, 2])
    left.subheader(f"{name} · {ticker}")
    right.markdown(
        f"<div style='text-align:right'><b style='color:{color}'>{title} {arrow}</b><br>"
        f"<span style='font-size:1.1rem'>{current:,.0f} ({rate:+.2f}%)</span></div>",
        unsafe_allow_html=True,
    )

    figure = make_subplots(specs=[[{"secondary_y": True}]])
    figure.add_trace(
        go.Scatter(x=display.index, y=display["외인 누적"], name="외인누적(억)", line={"color": "#2563eb", "width": 3}),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(x=display.index, y=display["기관 누적"], name="기관누적(억)", line={"color": "#f59e0b", "width": 3}),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(x=display.index, y=display["개인 누적"], name="개인누적(억)", line={"color": "#10b981", "width": 2}),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(x=display.index, y=display["Price"], name="주가", line={"color": "#ef4444", "dash": "dot"}),
        secondary_y=True,
    )
    figure.add_hline(y=0, line_dash="dash", line_color="#94a3b8")
    figure.update_layout(
        height=460,
        hovermode="x unified",
        margin={"l": 10, "r": 10, "t": 25, "b": 10},
        legend={"orientation": "h", "y": 1.08},
        dragmode=False,
    )
    st.plotly_chart(figure, width="stretch", config={"displaylogo": False, "scrollZoom": True})

    detail = display[
        ["Price", "F_억", "I_억", "P_억", "외인 누적", "기관 누적", "개인 누적"]
    ].iloc[::-1]
    detail.columns = ["주가", "외인 일일", "기관 일일", "개인 일일", "외인 누적", "기관 누적", "개인 누적"]
    detail.index = detail.index.strftime("%Y-%m-%d")
    st.dataframe(detail.style.format("{:,.1f}"), width="stretch")


st.title("📊 국내 수급 스캐너")
st.caption(
    "Streamlit은 저장된 장 마감 캐시만 읽습니다. KIS 토큰 발급과 대량 수집은 평일 장 마감 후 GitHub 배치 한 곳에서만 실행됩니다."
)

scan_cache = get_scan_cache(cache_file_version(CACHE_FILE))
batch_state = get_batch_state(cache_file_version(BATCH_STATE_FILE))
render_manual_refresh(scan_cache, batch_state)
markets = scan_cache.get("markets", {})
if not markets:
    st.error("수급 캐시가 없습니다. GitHub의 일일 배치 실행 상태를 확인해 주세요.")
    st.stop()

mode = st.radio(
    "분석 시장",
    ["KOSPI 200", "KOSDAQ 150", "전체 종목 검색"],
    horizontal=True,
    label_visibility="collapsed",
)
market_key = {"KOSPI 200": "kospi200", "KOSDAQ 150": "kosdaq150"}.get(mode)
market = markets.get(market_key, {}) if market_key else {}

if market_key:
    render_status(scan_cache, market)
    groups = normalized_groups(market)
    summary = market.get("summary", {})
    render_summary(summary)
    direction_label = st.segmented_control(
        "수급 방향",
        ["전체", "쌍끌이 매수", "엇갈림", "쌍끌이 매도"],
        default="전체",
        label_visibility="collapsed",
    )
    direction_key = {
        "쌍끌이 매수": "buy",
        "엇갈림": "mixed",
        "쌍끌이 매도": "sell",
    }.get(direction_label)
    active_directions = [direction_key] if direction_key else ["buy", "mixed", "sell"]
    entries = [item for key in active_directions for item in groups[key]]

    with st.expander(f"수급 분류표 · {len(entries)}종목", expanded=False):
        render_flow_table(entries, market.get("previous_direction_groups", {}))

    labels = [
        f"{item['name']} · {DIRECTION_META[item['direction']][1]}"
        for item in entries
    ]
    entry_by_label = dict(zip(labels, entries))
    if not labels:
        st.info("현재 선택 조건에 해당하는 종목이 없습니다.")
        st.stop()
    selector_options = labels
    stock_by_option = {
        label: (item["name"], item["ticker"])
        for label, item in entry_by_label.items()
    }
else:
    cached_symbols = {}
    for cached_market in markets.values():
        cached_symbols.update(cached_market.get("symbols", {}))
    try:
        all_symbols = get_all_symbols()
    except Exception:
        all_symbols = cached_symbols
    query = st.text_input("종목명 검색", placeholder="예: 삼성전자")
    candidates = {
        name: ticker
        for name, ticker in all_symbols.items()
        if not query or query.lower() in name.lower() or query in ticker
    }
    if not candidates:
        st.info("검색 결과가 없습니다.")
        st.stop()
    selector_options = list(candidates)
    stock_by_option = {
        name: (name, ticker)
        for name, ticker in candidates.items()
    }

selected_option = render_stock_navigation(selector_options)
selected_name, selected_ticker = stock_by_option[selected_option]

if not market_key:
    if selected_ticker not in {
        ticker
        for cached_market in markets.values()
        for ticker in cached_market.get("chart_data", {})
    }:
        st.info(
            "이 종목은 일일 캐시 대상(KOSPI 200·KOSDAQ 150) 밖입니다. "
            "KIS 토큰 단일 발급 원칙에 따라 웹에서 실시간 호출하지 않습니다."
        )
        st.stop()

period = st.select_slider(
    "차트 기간",
    options=[5, 10, 15, 20, 25, INVESTOR_CHART_MAX_ROWS],
    value=INVESTOR_CHART_MAX_ROWS,
)
st.caption("KIS 단일 조회 캐시 기준 · 최근 최대 30거래일")
chart_frame = find_chart_frame(scan_cache, selected_ticker)
if chart_frame.empty:
    st.warning("선택 종목의 차트 캐시가 없습니다. 다음 장 마감 배치에서 다시 확인해 주세요.")
else:
    render_stock_chart(selected_name, selected_ticker, chart_frame, period)

with st.expander("시스템 상태"):
    st.write(f"전체 캐시 기준일: **{format_target_date(scan_cache.get('target_date'))}**")
    st.write(f"캐시 생성: **{format_generated_at(scan_cache.get('generated_at_kst'))}**")
    st.write("Streamlit KIS 토큰 요청: **비활성화(읽기 전용)**")
