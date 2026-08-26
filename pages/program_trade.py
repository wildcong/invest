import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from market_data import (
    PROGRAM_TRADE_CACHE_FILE,
    cache_file_version,
    load_program_trade_cache,
)


@st.cache_data(max_entries=2, show_spinner=False)
def get_program_cache(cache_version: tuple[int, int]) -> dict:
    del cache_version
    return load_program_trade_cache()


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in (
        "non_arbitrage_net_억원",
        "arbitrage_net_억원",
        "total_program_net_억원",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna().sort_values("date").set_index("date")


def period_sum(frame: pd.DataFrame, days: int) -> float:
    return float(frame["non_arbitrage_net_억원"].tail(days).sum())


st.title("🇰🇷 비차익 프로그램매매")
st.caption(
    "KOSPI·KOSDAQ 비차익 순매수 거래대금을 장 마감 후 한 번 수집합니다. "
    "양수는 순매수, 음수는 순매도입니다."
)

cache = get_program_cache(cache_file_version(PROGRAM_TRADE_CACHE_FILE))
if not cache.get("markets"):
    st.warning(
        "아직 프로그램매매 캐시가 없습니다. 배포 후 첫 평일 장 마감 일일 배치가 "
        "완료되면 이 화면에 표시됩니다."
    )
    st.stop()

market_label = st.radio(
    "시장",
    ["KOSPI", "KOSDAQ"],
    horizontal=True,
    label_visibility="collapsed",
)
market_key = market_label.lower()
frame = rows_to_frame(cache["markets"].get(market_key, {}).get("rows", []))
if frame.empty:
    st.info(f"{market_label} 프로그램매매 데이터가 없습니다.")
    st.stop()

period = st.segmented_control(
    "조회 기간",
    [30, 60, 120, 250],
    default=60,
    format_func=lambda value: f"{value}거래일",
    label_visibility="collapsed",
)
display = frame.tail(period or 60).copy()
latest = display.iloc[-1]
metrics = st.columns(4)
metrics[0].metric("최근 비차익 순매수", f"{latest['non_arbitrage_net_억원']:,.0f}억원")
metrics[1].metric("5일 누적", f"{period_sum(frame, 5):,.0f}억원")
metrics[2].metric("20일 누적", f"{period_sum(frame, 20):,.0f}억원")
metrics[3].metric("최근 기준일", display.index[-1].strftime("%Y-%m-%d"))

display["비차익 누적"] = display["non_arbitrage_net_억원"].cumsum()
colors = [
    "#ef4444" if value >= 0 else "#2563eb"
    for value in display["non_arbitrage_net_억원"]
]
figure = make_subplots(specs=[[{"secondary_y": True}]])
figure.add_trace(
    go.Bar(
        x=display.index,
        y=display["non_arbitrage_net_억원"],
        name="비차익 일일 순매수",
        marker_color=colors,
    ),
    secondary_y=False,
)
figure.add_trace(
    go.Scatter(
        x=display.index,
        y=display["비차익 누적"],
        name="비차익 기간 누적",
        line={"color": "#111827", "width": 2.5},
    ),
    secondary_y=True,
)
figure.add_hline(y=0, line_dash="dash", line_color="#94a3b8")
figure.update_yaxes(title_text="일일 순매수(억원)", secondary_y=False)
figure.update_yaxes(title_text="기간 누적(억원)", secondary_y=True)
figure.update_layout(
    height=500,
    hovermode="x unified",
    margin={"l": 10, "r": 10, "t": 25, "b": 10},
    legend={"orientation": "h", "y": 1.08},
)
st.plotly_chart(figure, width="stretch", config={"displaylogo": False})

table = display[
    [
        "non_arbitrage_net_억원",
        "arbitrage_net_억원",
        "total_program_net_억원",
        "비차익 누적",
    ]
].iloc[::-1]
table.columns = ["비차익 순매수", "차익 순매수", "전체 프로그램", "비차익 기간누적"]
table.index = table.index.strftime("%Y-%m-%d")
st.dataframe(table.style.format("{:,.1f}"), width="stretch")
st.caption(f"출처: {cache.get('source', 'KIS')} · 단위: 억원")
