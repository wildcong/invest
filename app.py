import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import FinanceDataReader as fdr
import time

# ==========================================
# 🔒 보안 설정 (Streamlit Secrets)
# ==========================================
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"

st.set_page_config(page_title="수급 쌍끌이 스캐너", layout="wide")

# ==========================================
# 1. 데이터 수집 함수들
# ==========================================
@st.cache_data(ttl=86400)
def get_kospi200_list():
    try:
        df_kospi = fdr.StockListing('KOSPI')
        mcap_col = 'Marcap' if 'Marcap' in df_kospi.columns else 'MarCap'
        df_200 = df_kospi.sort_values(mcap_col, ascending=False).head(200)
        return dict(zip(df_200['Name'], df_200['Code']))
    except Exception:
        return {"삼성전자": "005930", "SK하이닉스": "000660"}

@st.cache_data(ttl=86000)
def get_access_token():
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body))
    return res.json().get("access_token") if res.status_code == 200 else None

@st.cache_data(ttl=3600, show_spinner=False)
def get_investor_data(ticker, access_token):
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {access_token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "FHKST01010900", "custtype": "P"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor", headers=headers, params=params)
    if res.status_code == 200:
        df = pd.DataFrame(res.json()['output'])
        if df.empty: return pd.DataFrame()
        df = df[['stck_bsop_date', 'stck_clpr', 'frgn_ntby_qty', 'orgn_ntby_qty']].copy()
        df.columns = ['Date', 'Price', 'Foreign', 'Institutional']
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['Price', 'Foreign', 'Institutional']:
            df[col] = pd.to_numeric(df[col])
        return df.sort_values('Date').set_index('Date')
    return pd.DataFrame()

# ==========================================
# 2. 전수 조사(Scanner) 로직
# ==========================================
def scan_all_stocks(stock_dict, token):
    valid_stocks = {}
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(stock_dict)
    
    for i, (name, ticker) in enumerate(stock_dict.items()):
        status_text.text(f"🚀 스캐닝 ({i+1}/{total}): {name}")
        df = get_investor_data(ticker, token)
        if not df.empty and len(df) >= 5:
            f_sum = df['Foreign'].tail(5).sum()
            i_sum = df['Institutional'].tail(5).sum()
            if f_sum > 0 and i_sum > 0:
                valid_stocks[name] = f"{name} (↑↑)"
            elif f_sum < 0 and i_sum < 0:
                valid_stocks[name] = f"{name} (↓↓)"
        progress_bar.progress((i + 1) / total)
        time.sleep(0.05)
    status_text.empty()
    progress_bar.empty()
    return valid_stocks

# ==========================================
# 3. 메인 화면 구성 및 필터링
# ==========================================
st.title("📊 KOSPI 200 쌍끌이 스캐너")

kospi_dict = get_kospi200_list()
token = get_access_token()

if 'current_idx' not in st.session_state:
    st.session_state.current_idx = 0

head_col1, head_col2 = st.columns([1.5, 1])

with head_col1:
    is_filtered = st.checkbox("🔥 최근 5일 +/- 동방향 종목만 필터링")

if is_filtered:
    if 'filtered_map' not in st.session_state:
        st.session_state.filtered_map = scan_all_stocks(kospi_dict, token)
    display_names = list(st.session_state.filtered_map.values())
    name_lookup = {v: k for k, v in st.session_state.filtered_map.items()}
else:
    display_names = list(kospi_dict.keys())
    name_lookup = {n: n for n in display_names}

if not display_names:
    st.warning("조건에 맞는 종목이 없습니다.")
    display_names = ["삼성전자"]
    name_lookup = {"삼성전자": "삼성전자"}

# --- 내비게이션 함수 ---
def go_prev():
    if st.session_state.current_idx > 0:
        st.session_state.current_idx -= 1
        st.session_state.stock_selector = display_names[st.session_state.current_idx]

def go_next():
    if st.session_state.current_idx < len(display_names) - 1:
        st.session_state.current_idx += 1
        st.session_state.stock_selector = display_names[st.session_state.current_idx]

def on_change():
    if 'stock_selector' in st.session_state:
        if st.session_state.stock_selector in display_names:
            st.session_state.current_idx = display_names.index(st.session_state.stock_selector)

# 컨트롤러 레이아웃
c1, c2, c3 = st.columns([1, 2, 1])
with c1: st.button("⬅️ 이전", on_click=go_prev, use_container_width=True)
with c2:
    if st.session_state.current_idx >= len(display_names): st.session_state.current_idx = 0
    selected_display_name = st.selectbox("종목", display_names, index=st.session_state.current_idx, 
                                         key="stock_selector", on_change=on_change, label_visibility="collapsed")
with c3: st.button("다음 ➡️", on_click=go_next, use_container_width=True)

selected_real_name = name_lookup.get(selected_display_name, selected_display_name)

# ==========================================
# 4. 분석 및 시각화
# ==========================================
selected_ticker = kospi_dict.get(selected_real_name, "005930")
df = get_investor_data(selected_ticker, token)

if not df.empty:
    # --- 주가 및 등락률 복구 ---
    curr_p = df['Price'].iloc[-1]
    prev_p = df['Price'].iloc[-2]
    diff = curr_p - prev_p
    ratio = (diff / prev_p) * 100
    
    # 방향성 뱃지 로직
    f_sum = df['Foreign'].tail(5).sum()
    i_sum = df['Institutional'].tail(5).sum()
    if f_sum > 0 and i_sum > 0:
        badge = '<span style="background-color: #ff4b4b; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8rem;">쌍끌이 매수 ↑↑</span>'
    elif f_sum < 0 and i_sum < 0:
        badge = '<span style="background-color: #31333f; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8rem;">쌍끌이 매도 ↓↓</span>'
    else:
        badge = '<span style="background-color: #f0f2f6; color: #31333f; padding: 2px 6px; border-radius: 4px; font-size: 0.8rem;">엇갈림</span>'

    p_color = "red" if diff > 0 else "blue" if diff < 0 else "gray"
    p_arrow = "▲" if diff > 0 else "▼" if diff < 0 else ""

    with head_col2:
        st.markdown(f"""
            <div style="text-align: right; line-height: 1.5;">
                <div>{badge}</div>
                <div style="font-size: 1.1rem; font-weight: bold;">
                    {selected_real_name} : 
                    <span style="color: {p_color};">
                        {curr_p:,.0f} ({p_arrow}{abs(diff):,.0f}, {ratio:.2f}%)
                    </span>
                </div>
            </div>
        """, unsafe_allow_html=True)

    # 데이터 계산 및 차트
    df['F_억'] = (df['Foreign'] * df['Price']) / 100000000
    df['I_억'] = (df['Institutional'] * df['Price']) / 100000000
    df['F_누적'] = df['F_억'].cumsum()
    df['I_누적'] = df['I_억'].cumsum()

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=df.index, y=df['F_누적'], name='외인누적', line=dict(color='blue', width=3)), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df['I_누적'], name='기관누적', line=dict(color='orange', width=3)), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df['Price'], name='주가', line=dict(color='red', width=1.5, dash='dot')), secondary_y=True)
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    
    fig.update_layout(title=f"<b>{selected_real_name}</b> 수급/주가 추세", hovermode="x unified", height=450, 
                      margin=dict(l=5, r=5, t=50, b=5), legend=dict(orientation="h", y=1.1, x=0.5, xanchor='center'))
    st.plotly_chart(fig, use_container_width=True)

    st.write("##### 📋 상세 내역 (단위: 억원)")
    result_df = df[['Price', 'F_억', 'I_억', 'F_누적', 'I_누적']].iloc[::-1].copy()
    result_df.columns = ['주가', '외인_일일', '기관_일일', '외인_누적', '기관_누적']
    st.dataframe(result_df.style.format("{:,.1f}"), use_container_width=True)
