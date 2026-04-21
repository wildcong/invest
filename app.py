import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from scanner import classify_5day_direction, get_investor_data as fetch_investor_data
from scanner import get_access_token as fetch_access_token
from scanner import get_stock_lists as fetch_stock_lists
from scanner import get_target_date, load_scan_cache

# ==========================================
# 🔒 보안 설정 (Streamlit Secrets)
# ==========================================
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"

st.set_page_config(page_title="수급 쌍끌이 스캐너", layout="wide")
st.markdown(
    """
    <style>
    div[data-testid="stPlotlyChart"] {
        touch-action: pan-y pinch-zoom !important;
    }
    div[data-testid="stPlotlyChart"] .js-plotly-plot,
    div[data-testid="stPlotlyChart"] .plot-container,
    div[data-testid="stPlotlyChart"] .plotly,
    div[data-testid="stPlotlyChart"] .svg-container {
        touch-action: pan-y pinch-zoom !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
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

def scan_all_stocks(stock_dict, token):
    valid_stocks = {}
    summary = {"buy": 0, "mixed": 0, "sell": 0, "scanned": 0}
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(stock_dict)
    
    for i, (name, ticker) in enumerate(stock_dict.items()):
        status_text.text(f"🚀 스캔 중 ({i+1}/{total}): {name}")
        df = get_investor_data(ticker, token)
        if not df.empty and len(df) >= 5:
            direction = classify_5day_direction(df)
            summary["scanned"] += 1
            summary[direction] += 1
            if direction == "buy":
                valid_stocks[name] = f"{name} (↑↑)"
            elif direction == "sell":
                valid_stocks[name] = f"{name} (↓↓)"
                
        progress_bar.progress((i + 1) / total)
        # ⚡ 0.05초로 복구
        time.sleep(0.05)
        
    status_text.empty()
    progress_bar.empty()
    return valid_stocks, summary

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

if 'current_idx' not in st.session_state:
    st.session_state.current_idx = 0

# 🎯 탭에 따른 로직 분리 (전체 종목 탭은 스캔 불가 처리)
if market_mode == "🔵 KOSPI 200":
    target_dict = dict_k200
    allow_scan = True
    market_cache_key = "kospi200"
elif market_mode == "🟢 KOSDAQ 150":
    target_dict = dict_kq150
    allow_scan = True
    market_cache_key = "kosdaq150"
else:
    target_dict = dict_all
    allow_scan = False
    market_cache_key = None

scan_cache = get_scan_cache()
cached_market = scan_cache.get("markets", {}).get(market_cache_key, {}) if market_cache_key else {}
cached_generated_at = scan_cache.get("generated_at_kst")

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
    if 'filtered_map' not in st.session_state or 'scan_summary' not in st.session_state:
        if cached_market.get("summary"):
            st.session_state.filtered_map = cached_market.get("filtered_map", {})
            st.session_state.scan_summary = cached_market["summary"]
        elif token:
            filtered_map, scan_summary = scan_all_stocks(target_dict, token)
            st.session_state.filtered_map = filtered_map
            st.session_state.scan_summary = scan_summary
        else:
            st.error("API 토큰 발급 실패")
            st.stop()
    if market_mode in ("🔵 KOSPI 200", "🟢 KOSDAQ 150"):
        scan_summary = st.session_state.scan_summary
        updated_text = ""
        if cached_generated_at:
            updated_text = f" | 자동갱신 {cached_generated_at[:16].replace('T', ' ')}"
        summary_placeholder.markdown(
            (
                "<div style='font-size:0.82rem; line-height:1.5; white-space:nowrap; margin-top: 2px;'>"
                f"쌍끌이매수 <b>{scan_summary['buy']}</b> | "
                f"엇갈림 <b>{scan_summary['mixed']}</b> | "
                f"쌍끌이매도 <b>{scan_summary['sell']}</b>"
                f"<span style='color:#6b7280;'> ({scan_summary['scanned']}/{len(target_dict)} 집계)</span>"
                f"<span style='color:#6b7280;'>{updated_text}</span>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    display_names = list(st.session_state.filtered_map.values())
    name_lookup = {v: k for k, v in st.session_state.filtered_map.items()}
else:
    display_names = list(target_dict.keys())
    name_lookup = {n: n for n in display_names}
    if allow_scan:
        summary_placeholder.empty()

if not display_names:
    st.warning("조건에 맞는 종목이 없습니다.")
    display_names = ["삼성전자"]; name_lookup = {"삼성전자": "삼성전자"}

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
with c1: st.button("⬅️ 이전", on_click=go_prev, use_container_width=True)
with c2:
    if st.session_state.current_idx >= len(display_names): st.session_state.current_idx = 0
    selected_disp = st.selectbox("종목 선택", display_names, index=st.session_state.current_idx, 
                                 key="stock_selector", on_change=on_change, label_visibility="collapsed")
with c3: st.button("다음 ➡️", on_click=go_next, use_container_width=True)

selected_real = name_lookup.get(selected_disp, selected_disp)
selected_ticker = target_dict.get(selected_real, "005930")

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
            use_container_width=True,
            config={
                "scrollZoom": True,
                "doubleClick": False,
                "displaylogo": False,
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
            
        st.dataframe(styled_df, use_container_width=True)

        st.markdown("<p style='font-size: 0.8rem; color: gray; margin-top: -10px;'>💡 당일 수급 데이터는 16:30에 추가되며, 해당 시간 이후 데이터가 초기화 및 업데이트됩니다.</p>", unsafe_allow_html=True)
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
