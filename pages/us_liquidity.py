from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from market_data import load_us_liquidity_cache


@st.cache_data(ttl=300, show_spinner=False)
def get_liquidity_cache() -> dict:
    return load_us_liquidity_cache()


def rows_to_frame(rows: list[dict], years: int | None) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value_십억달러"], errors="coerce")
    frame = frame.dropna().sort_values("date")
    if years:
        cutoff = frame["date"].max() - pd.DateOffset(years=years)
        frame = frame[frame["date"] >= cutoff]
    return frame


def render_series(series: dict, years: int | None) -> None:
    frame = rows_to_frame(series.get("rows", []), years)
    if frame.empty:
        st.info(f"{series.get('label', '지표')} 데이터가 없습니다.")
        return
    latest = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) > 1 else latest
    delta = latest["value"] - previous["value"]
    st.metric(
        series["label"],
        f"${latest['value']:,.1f}B",
        f"{delta:+,.1f}B · {latest['date']:%Y-%m-%d}",
    )
    figure = go.Figure(
        go.Scatter(
            x=frame["date"],
            y=frame["value"],
            mode="lines",
            fill="tozeroy",
            line={"color": "#2563eb", "width": 2},
            fillcolor="rgba(37, 99, 235, 0.10)",
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.1f}B<extra></extra>",
        )
    )
    figure.update_layout(
        height=300,
        margin={"l": 5, "r": 5, "t": 5, "b": 5},
        yaxis_title="십억 달러",
        showlegend=False,
        hovermode="x",
    )
    st.plotly_chart(figure, width="stretch", config={"displaylogo": False})
    st.caption(f"{series['frequency']} · FRED {series['series_id']}")


st.title("🇺🇸 미국 유동성")
st.caption(
    "TGA, M2, 연준 역레포, 지급준비금을 서로 다른 발표 주기 그대로 비교합니다. "
    "수치는 모두 십억 달러($B)로 통일했습니다."
)

cache = get_liquidity_cache()
series_map = cache.get("series", {})
if not series_map:
    st.warning("미국 유동성 캐시가 없습니다. 다음 일일 배치에서 FRED 데이터를 갱신합니다.")
    st.stop()

range_label = st.segmented_control(
    "기간",
    ["1년", "3년", "5년", "전체"],
    default="3년",
    label_visibility="collapsed",
)
years = {"1년": 1, "3년": 3, "5년": 5, "전체": None}[range_label or "3년"]

for row_keys in (("tga", "m2"), ("reverse_repo", "reserve_balances")):
    columns = st.columns(2)
    for column, key in zip(columns, row_keys):
        with column:
            render_series(series_map.get(key, {}), years)

try:
    generated = datetime.fromisoformat(cache.get("generated_at_utc", ""))
    generated_text = generated.strftime("%Y-%m-%d %H:%M UTC")
except ValueError:
    generated_text = cache.get("generated_at_utc", "-")
st.caption(f"캐시 생성: {generated_text} · 출처: Federal Reserve Economic Data (FRED)")
