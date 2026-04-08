import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import FinanceDataReader as fdr

# ==========================================
# 🔒 보안 설정 (Streamlit Secrets)
# ==========================================
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"

# 페이지 설정 (모바일을 위해 여백 최소화)
st.set_page_config(page_title="수급 분석기", layout="wide")

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
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET,
        "tr_id": "FHKST01010900", "custtype": "P"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor", headers=headers, params=params)
    if res.status_code == 200:
        df = pd.DataFrame(res.json()['output'])
        if df.empty: return pd.DataFrame()
        df = df[['stck_bsop_date', 'stck_clpr', 'frgn_ntby_qty', 'orgn_ntby_qty']].copy()
        df.columns = ['Date', '주가(원)', '외국인_일일(주)', '기관_일일(주)']
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['주가(원)', '외국인_일일(주)', '기관_일일(주)']:
            df[col] = pd.to_numeric(df[col])
        return df.sort_values('Date').set_index('Date')
    return pd.DataFrame()

# ==========================================
# 2. 본문 인터페이스 및 콜백 로직
# ==========================================
st.title("📈 수급 흐름 분석")

with st.spinner("로딩 중..."):
    kospi_dict = get_kospi200_list()
    kospi_names = list(kospi_dict.keys())

# 세션 상태 초기화
if 'current_idx' not in st.session_state:
    st.session_state.current_idx = 0
if 'stock_selector' not in st.session_state:
    st.session_state.stock_selector = kospi_names[0]

# 콜백 함수들
def update_from_selectbox():
    st.session_state.current_idx = kospi_names.index(st.session_state.stock_selector)

def go_prev():
    if st.session_state.current_idx > 0:
        st.session_state.current_idx -= 1
        st.session_state.stock_selector = kospi_names[st.session_state.current_idx]

def go_next():
    if st.session_state.current_idx < len(kospi_names) - 1:
        st.session_state.current_idx += 1
        st.session_state.stock_selector = kospi_names[st.session_state.current_idx]

# --- 본문 상단 컨트롤러 레이아웃 ---
# 모바일에서 버튼이 나란히 보이도록 3분할
c1, c2, c3 = st.columns([1, 2, 1])
with c1:
    st.button("⬅️ 이전", on_click=go_prev, use_container_width=True)
with c2:
    selected_name = st.selectbox(
        "종목 선택", 
        kospi_names, 
        key="stock_selector",
        on_change=update_from_selectbox,
        label_visibility="collapsed" # 공간 절약을 위해 라벨 숨김
    )
with c3:
    st.button("다음 ➡️", on_click=go_next, use_container_width=True)

# 기간 설정 슬라이더 (본문에 배치)
period = st.select_slider(
    "분석 기간 (영업일)", 
    options=[5, 10, 15, 20, 25, 30], 
    value=30
)

# ==========================================
# 3. 분석 및 시각화
# ==========================================
selected_ticker = kospi_dict[selected_name]
token = get_access_token()

if token:
    df = get_investor_data(selected_ticker, token)
    if not df.empty:
        df = df.tail(period).copy()
        
        # 억원 단위 환산
        df['외국인_일일(억원)'] = (df['외국인_일일(주)'] * df['주가(원)']) / 100000000
        df['기관_일일(억원)'] = (df['기관_일일(주)'] * df['주가(원)']) / 100000000
        df['외국인_누적(억원)'] = df['외국인_일일(억원)'].cumsum()
        df['기관_누적(억원)'] = df['기관_일일(억원)'].cumsum()

        # 차트
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=df.index, y=df['외국인_누적(억원)'], name='외인누적', line=dict(color='blue', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적(억원)'], name='기관누적', line=dict(color='orange', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df.index, y=df['주가(원)'], name='주가', line=dict(color='red', width=1.5, dash='dot')), secondary_y=True)
        fig.add_hline(y=0, line_dash="dash", line_color="gray")

        fig.update_layout(
            title=dict(text=f"<b>{selected_name}</b> 수급 및 주가", font=dict(size=18)),
            hovermode="x unified",
            height=450, # 모바일에서 한 화면에 보이도록 높이 조절
            margin=dict(l=10, r=10, t=50, b=50),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

        # 하단 데이터 표 (모바일 가독성을 위해 간소화)
        st.write("##### 📋 최근 데이터 (단위: 억원)")
        display_df = df[['주가(원)', '외국인_누적(억원)', '기관_누적(억원)']].iloc[::-1]
        st.dataframe(display_df.style.format("{:,.1f}"), use_container_width=True)
    else:
        st.warning("데이터가 없습니다.")
