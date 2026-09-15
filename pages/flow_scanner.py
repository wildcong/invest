from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from market_data import cache_file_version
from manual_refresh import (
    ManualRefreshError,
    load_manual_refresh_state,
    manual_refresh_availability,
    run_direct_scan_refresh,
)
from scanner import (
    CACHE_FILE,
    INVESTOR_CHART_MAX_ROWS,
    cache_has_target_date,
    classify_5day_direction,
    get_stock_lists,
    get_target_date,
    load_scan_cache,
    scan_coverage,
    valid_chart_rows,
)
from stock_universe import STOCK_UNIVERSE_SOURCE
from trading_calendar import REVIEWED_THROUGH_YEAR, kis_collection_ready_at


KST = timezone(timedelta(hours=9))
STOCK_SELECTOR_KEY = "flow_stock_selector"
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


def get_kis_credentials() -> tuple[str, str]:
    def secret(name: str) -> str:
        try:
            return str(st.secrets.get(name, "")).strip()
        except Exception:
            return ""

    manual_key = secret("KIS_MANUAL_APP_KEY")
    manual_secret = secret("KIS_MANUAL_APP_SECRET")
    if manual_key and manual_secret:
        return manual_key, manual_secret
    return secret("KIS_APP_KEY"), secret("KIS_APP_SECRET")


def render_manual_refresh(scan_cache: dict) -> None:
    target_date = get_target_date()
    completed = cache_has_target_date(scan_cache, target_date)
    availability = manual_refresh_availability(target_date)
    app_key, app_secret = get_kis_credentials()
    credentials_ready = bool(app_key and app_secret)
    now_kst = datetime.now(KST)
    ready_at = kis_collection_ready_at(now_kst)
    waiting_for_kis = ready_at is not None and now_kst < ready_at

    status_column, button_column = st.columns([4, 1])
    with status_column:
        if completed:
            st.caption(
                f"{format_target_date(target_date)} 배치 완료 · 같은 거래일은 중복 실행하지 않습니다."
            )
        else:
            if availability.status == "running":
                st.info("수동 갱신이 실행 중입니다. 다른 요청은 중복 실행되지 않습니다.")
            elif availability.status == "cooldown" and availability.retry_at:
                st.caption(
                    f"직전 조회 후 대기 중 · {availability.retry_at:%H:%M KST}부터 재시도할 수 있습니다."
                )
            elif not credentials_ready:
                st.warning("수동 직접조회를 사용하려면 Streamlit에 KIS API 키 설정이 필요합니다.")
            elif waiting_for_kis:
                st.caption(
                    f"당일 KIS 데이터 준비 대기 중 · {ready_at:%H:%M KST}부터 갱신할 수 있습니다."
                )
            else:
                st.caption(
                    f"갱신 대상 {format_target_date(target_date)} · 버튼을 누르면 KIS에서 직접 조회합니다."
                )

    with button_column:
        if st.button(
            "🔄 수동 갱신",
            key="manual_market_refresh",
            width="stretch",
            type="primary",
            disabled=completed or not availability.can_run or waiting_for_kis,
            help="KIS 수급 데이터를 직접 조회해 이 페이지의 캐시를 갱신합니다.",
        ):
            if not credentials_ready:
                st.error(
                    "Streamlit Secrets에 KIS_APP_KEY와 KIS_APP_SECRET을 설정해야 합니다."
                )
                return
            try:
                with st.spinner("KIS 수급 데이터를 조회하는 중입니다. 창을 닫지 마세요.", show_time=True):
                    result = run_direct_scan_refresh(app_key, app_secret)
            except ManualRefreshError as exc:
                st.error(str(exc))
                return

            get_scan_cache.clear()
            get_all_symbols.clear()
            st.session_state["manual_refresh_notice"] = {
                "status": result.status,
                "message": result.message,
            }
            st.rerun()


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    required = ["Date", "Price", "F_억", "I_억", "P_억"]
    for column in required:
        if column not in frame:
            return pd.DataFrame()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["Date", "Price"]).sort_values("Date").set_index("Date")


def find_chart_frame(scan_cache: dict, ticker: str) -> pd.DataFrame:
    for market in scan_cache.get("markets", {}).values():
        chart_rows = market.get("chart_data", {}).get(ticker)
        if chart_rows and valid_chart_rows(chart_rows, market.get("target_date") or get_target_date()):
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
            rows = market.get("chart_data", {}).get(item["ticker"], [])
            if not valid_chart_rows(rows, market.get("target_date") or get_target_date()):
                continue
            item["as_of"] = rows[-1].get("Date") if rows else None
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
        f"수집 목표일 {format_target_date(cached_date)}"
    )
    size = 200 if market is scan_cache.get("markets", {}).get("kospi200") else 150
    coverage = scan_coverage(market, expected_date, size)
    universe = scan_cache.get("universe", {})
    if universe.get("as_of"):
        source_label = (
            "한국투자증권 Open API 제공"
            if universe.get("source") == STOCK_UNIVERSE_SOURCE
            else "이전 방식의 캐시 · KIS 재수집 대기"
        )
        st.caption(
            f"종목 선정 목록 기준일: {format_target_date(universe['as_of'])} "
            f"· {source_label}"
        )
        if universe["as_of"] != expected_date:
            st.warning(
                "현재 캐시는 이전 기준일의 종목 목록입니다. 다음 KIS 갱신에서 "
                "당일 시가총액 목록을 다시 조회합니다."
            )
    if cached_date == expected_date and coverage["current"] == size:
        st.success(message)
    else:
        st.warning(
            f"{message} · 현재 목표일 {format_target_date(expected_date)} 데이터 확인 "
            f"{coverage['current']}/{size}종목. 일부 미갱신·누락은 다음 배치에서 재시도합니다. "
            "미갱신 사유는 확인되지 않았으며, 거래정지를 의미하지 않습니다."
        )
    if coverage["stale"] or coverage["missing"] or coverage["invalid"]:
        with st.expander("미갱신·누락 종목"):
            st.write("이전 자료만 있음: " + (", ".join(coverage["stale"]) or "없음"))
            st.write("자료 부족·누락: " + (", ".join(coverage["missing"]) or "없음"))
            st.write("날짜·수치 검증 실패: " + (", ".join(coverage["invalid"]) or "없음"))


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
                "실제 기준일": item.get("as_of") or "확인 필요",
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
    actual_date = frame.index[-1].strftime("%Y%m%d")
    st.caption(f"이 종목의 실제 수급 기준일: {format_target_date(actual_date)}")
    if actual_date != get_target_date():
        st.warning("목표 거래일 자료가 아직 없습니다. 아래 수급·가격은 표시된 실제 기준일 자료입니다.")
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
if datetime.now(KST).year > REVIEWED_THROUGH_YEAR:
    st.caption("올해 거래일 달력 검토가 필요합니다. 검토 전까지 16:45 이후 보수적으로 수집하고 원자료 날짜를 확인합니다.")
st.caption(
    "평소에는 저장된 장 마감 캐시를 읽고, 수동 갱신 버튼을 누른 경우에만 KIS 수급 데이터를 직접 조회합니다."
)

scan_cache = get_scan_cache(cache_file_version(CACHE_FILE))
manual_notice = st.session_state.pop("manual_refresh_notice", None)
if manual_notice:
    if manual_notice.get("status") in {"success", "already_current"}:
        st.success(manual_notice.get("message", "수동 갱신이 완료됐습니다."))
    else:
        st.warning(manual_notice.get("message", "일부 데이터만 갱신됐습니다."))
render_manual_refresh(scan_cache)
markets = scan_cache.get("markets", {})
if not markets:
    st.error("수급 캐시가 없습니다. GitHub의 일일 배치 실행 상태를 확인해 주세요.")
    st.stop()

mode = st.radio(
    "분석 시장",
    ["KOSPI 시가총액 상위 200", "KOSDAQ 시가총액 상위 150", "전체 종목 검색"],
    horizontal=True,
    label_visibility="collapsed",
)
market_key = {"KOSPI 시가총액 상위 200": "kospi200", "KOSDAQ 시가총액 상위 150": "kosdaq150"}.get(mode)
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
            "이 종목은 일일 캐시 대상(KOSPI·KOSDAQ 시가총액 상위 200·150종목) 밖입니다. "
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
    manual_state = load_manual_refresh_state()
    st.write("Streamlit KIS 조회: **수동 갱신 버튼에서만 활성화**")
    if manual_state.get("finished_at_kst"):
        st.write(
            f"마지막 수동 갱신: **{format_generated_at(manual_state.get('finished_at_kst'))}** "
            f"({manual_state.get('status', '-')})"
        )
