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
        df.columns = ['Date', '주가', '외인_일일', '기관_일일']
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['주가', '외인_일일', '기관_일일']:
            df[col] = pd.to_numeric(df[col])
        return df.sort_values('Date').set_index('Date')
    return pd.DataFrame()

# ==========================================
# 2. 전수 조사(Scanner) 로직
# ==========================================
def scan_all_stocks(stock_dict, token):
    valid_stocks = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total = len(stock_dict)
    for i, (name, ticker) in enumerate(stock_dict.items()):
        status_text.text(f"🚀 스캔 중 ({i+1}/{total}): {name}")
        df = get_investor_data(ticker, token)
        if not df.empty and len(df) >= 2:
            # 최근 5일 기준 누적 기울기 방향 체크 (간소화된 변곡점 로직)
            df_recent = df.tail(5).copy()
            f_slope = df_recent['외인_일일'].sum() # 최근 5일 합계 방향
            i_slope = df_recent['기관_일일'].sum()
            
            # 둘 다 양수(쌍끌이 매수)거나 둘 다 음수(쌍끌이 매도)인 경우
            if (f_slope > 0 and i_slope > 0) or (f_slope < 0 and i_slope < 0):
                valid_stocks.append(name)
        
        progress_bar.progress((i + 1) / total)
        time.sleep(0.05) # API 초당 호출 제한 방어
        
    status_text.empty()
    progress_bar.empty()
    return valid_stocks

# ==========================================
# 3. 메인 인터페이스
# ==========================================
st.title("📊 KOSPI 200 쌍끌이 탐지기")

kospi_dict = get_kospi200_list()
full_names = list(kospi_dict.keys())
token = get_access_token()

# --- 필터링 옵션 ---
is_filtered = st.checkbox("🔥 최근 5일 동방향(쌍끌이) 종목만 보기")

# 필터링 실행
if is_filtered:
    if 'filtered_list' not in st.session_state:
        st.session_state.filtered_list = scan_all_stocks(kospi_dict, token)
    display_names = st.session_state.filtered_list
else:
    display_names = full_names

if not display_names:
    st.warning("조건에 맞는 종목이 없습니다.")
    display_names = ["삼성전자"]

# --- 네비게이션 로직 ---
if 'current_idx' not in st.session_state: st.session_state.current_idx = 0

def go_prev():
    if st.session_state.current_idx > 0: st.session_state.current_idx -= 1
def go_next():
    if st.session_state.current_idx < len(display_names) - 1: st.session_state.current_idx += 1

# --- 상단 컨트롤러 ---
c1, c2, c3 = st.columns([1, 2, 1])
with c1: st.button("⬅️ 이전", on_click=go_prev, use_container_width=True)
with c2:
    # 인덱스 범위 초과 방어
    if st.session_state.current_idx >= len(display_names): st.session_state.current_idx = 0
    selected_name = st.selectbox("종목 선택", display_names, index=st.session_state.current_idx, label_visibility="collapsed")
with c3: st.button("다음 ➡️", on_click=go_next, use_container_width=True)

# ==========================================
# 4. 분석 및 그래프
# ==========================================
selected_ticker = kospi_dict.get(selected_name, "005930")
df = get_investor_data(selected_ticker, token)

if not df.empty:
    # 억원 환산
    df['외인_일일(억)'] = (df['외인_일일'] * df['주가']) / 100000000
    df['기관_일일(억)'] = (df['기관_일일'] * df['주가']) / 100000000
    df['외인_누적(억)'] = df['외인_일일(억)'].cumsum()
    df['기관_누적(억)'] = df['기관_일일(억)'].cumsum()

    # 차트
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=df.index, y=df['외인_누적(억)'], name='외인누적', line=dict(color='blue', width=3)), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적(억)'], name='기관누적', line=dict(color='orange', width=3)), secondary_y=False)
    fig.add_trace(go.Scatter(x=df.index, y=df['주가'], name='주가', line=dict(color='red', width=1.5, dash='dot')), secondary_y=True)
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    
    fig.update_layout(title=f"<b>{selected_name}</b> 자금 흐름", hovermode="x unified", height=450, 
                      margin=dict(l=10, r=10, t=50, b=10), legend=dict(orientation="h", y=1.1, x=0.5, xanchor='center'))
    st.plotly_chart(fig, use_container_width=True)

    # 하단 표 (요청하신 일일 순매수 복구)
    st.write("##### 📋 상세 수급 (단위: 억원)")
    display_df = df[['주가', '외인_일일(억)', '기관_일일(억)', '외인_누적(억)', '기관_누적(억)']].iloc[::-1]
    st.dataframe(display_df.style.format("{:,.1f}"), use_container_width=True)
