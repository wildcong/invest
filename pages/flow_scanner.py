from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from scanner import (
    INVESTOR_CHART_MAX_ROWS,
    classify_5day_direction,
    get_stock_lists,
    get_target_date,
    load_scan_cache,
)


KST = timezone(timedelta(hours=9))
DIRECTION_META = {
    "buy": ("쌍끌이 매수", "↑↑", "#ef4444"),
    "mixed": ("엇갈림", "↕", "#64748b"),
    "sell": ("쌍끌이 매도", "↓↓", "#2563eb"),
}


@st.cache_data(ttl=300, show_spinner=False)
def get_scan_cache() -> dict:
    return load_scan_cache()


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
            "다음 평일 16:00 배치에서 갱신됩니다."
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

scan_cache = get_scan_cache()
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
    selected_label = st.selectbox("종목 선택", labels)
    selected = entry_by_label[selected_label]
    selected_name = selected["name"]
    selected_ticker = selected["ticker"]
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
    selected_name = st.selectbox("종목 선택", list(candidates))
    selected_ticker = candidates[selected_name]
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
