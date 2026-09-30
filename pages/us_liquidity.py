from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard_theme import style_chart
from market_data import (
    FRED_SERIES,
    US_LIQUIDITY_CACHE_FILE,
    cache_file_version,
    classify_liquidity_effect,
    load_us_liquidity_cache,
)


@st.cache_data(max_entries=2, show_spinner=False)
def get_liquidity_cache(cache_version: tuple[int, int]) -> dict:
    del cache_version
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


def render_series(key: str, series: dict, years: int | None) -> None:
    metadata = {**FRED_SERIES.get(key, {}), **series}
    frame = rows_to_frame(series.get("rows", []), years)
    if frame.empty:
        st.info(f"{series.get('label', '지표')} 데이터가 없습니다.")
        return
    latest = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) > 1 else latest
    delta = latest["value"] - previous["value"]
    relation = metadata.get("liquidity_relation", "direct")
    is_inverse = relation == "inverse"
    relation_label = metadata.get("relation_label", "정방향")
    line_color = "#f59e0b" if is_inverse else "#10b981"
    fill_color = (
        "rgba(245, 158, 11, 0.12)"
        if is_inverse
        else "rgba(16, 185, 129, 0.12)"
    )
    precision = 3 if abs(latest["value"]) < 10 else 1
    effect = classify_liquidity_effect(delta, relation)
    st.markdown(f"##### {metadata.get('label', key)}")
    st.caption(
        f"{relation_label} · "
        + ("하락할수록 유동성 확대 방향" if is_inverse else "상승할수록 유동성 확대 방향")
    )
    st.metric(
        "최근 발표값",
        "$" + f"{latest['value']:,.{precision}f}B",
        f"{delta:+,.{precision}f}B · {effect}",
        delta_color="inverse" if is_inverse else "normal",
    )
    figure = go.Figure(
        go.Scatter(
            x=frame["date"],
            y=frame["value"],
            mode="lines",
            fill="tozeroy",
            line={"color": line_color, "width": 2},
            fillcolor=fill_color,
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.1f}B<extra></extra>",
        )
    )
    figure.update_layout(
        yaxis_title="십억 달러",
        showlegend=False,
        hovermode="x",
    )
    style_chart(figure, height=280)
    st.plotly_chart(
        figure,
        key=f"us_liquidity_{key}",
        width="stretch",
        config={"displaylogo": False},
    )
    st.caption(
        f"최근 기준일 {latest['date']:%Y-%m-%d} · "
        f"{series['frequency']} · FRED {series['series_id']}"
    )


st.title("미국 유동성")
st.caption(
    "네 가지 지표로 살펴보는 미국의 자금 흐름 · 단위: 십억 달러($B)"
)

cache = get_liquidity_cache(cache_file_version(US_LIQUIDITY_CACHE_FILE))
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
            with st.container(border=True):
                render_series(key, series_map.get(key, {}), years)

with st.expander("지표 읽는 법", expanded=False):
    st.write(
        "정방향 지표는 상승할 때, 역방향 지표는 하락할 때 유동성 확대 방향으로 읽습니다. "
        "각 지표의 발표 주기가 다르며, 증감은 직전 발표값과 비교합니다. "
        "주가의 즉시 매수·매도 신호를 뜻하지는 않습니다."
    )
    for key in ("tga", "m2", "reverse_repo", "reserve_balances"):
        metadata = {**FRED_SERIES[key], **series_map.get(key, {})}
        st.markdown(f"**{metadata['label']} · {metadata['relation_label']}**")
        st.write(metadata.get("relation_summary", ""))
        st.caption(metadata.get("interpretation", ""))

try:
    generated = datetime.fromisoformat(cache.get("generated_at_utc", ""))
    generated_text = generated.strftime("%Y-%m-%d %H:%M UTC")
except ValueError:
    generated_text = cache.get("generated_at_utc", "-")
st.caption(f"캐시 생성: {generated_text} · 출처: Federal Reserve Economic Data (FRED)")
