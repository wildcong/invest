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

st.set_page_config(page_title="KOSPI 200 누적/순매수 분석", layout="wide")

# ==========================================
# 1. KOSPI 200 종목 리스트 가져오기
# ==========================================
@st.cache_data(ttl=86400)
def get_kospi200_list():
    try:
        df_kospi = fdr.StockListing('KOSPI')
        # 시가총액 기준 상위 200개 선정
        mcap_col = 'Marcap' if 'Marcap' in df_kospi.columns else 'MarCap'
        df_200 = df_kospi.sort_values(mcap_col, ascending=False).head(200)
        return dict(zip(df_200['Name'], df_200['Code']))
    except Exception as e:
        return {"삼성전자": "005930", "SK하이닉스": "000660"}

# ==========================================
# 2. 한국투자증권 API 통신
# ==========================================
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
        
        df = df[['stck_bsop_date', 'frgn_ntby_qty', 'orgn_ntby_qty']].copy()
        df.columns = ['Date', '외국인_일일', '기관_일일']
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['외국인_일일', '기관_일일']:
            df[col] = pd.to_numeric(df[col])
            
        return df.sort_values('Date').set_index('Date')
    return pd.DataFrame()

# ==========================================
# 3. 메인 화면 구성 (자동 조회)
# ==========================================
st.title("📊 KOSPI 200 수급 분석 (일일 & 누적)")

with st.spinner("종목 리스트 로딩 중..."):
    kospi_dict = get_kospi200_list()

st.sidebar.header("설정")
selected_name = st.sidebar.selectbox("종목 선택", list(kospi_dict.keys()))
selected_ticker = kospi_dict[selected_name]

# 누적 계산을 위한 기간 설정 (KIS API는 최대 30일 제공)
period = st.sidebar.slider("분석 기간 (최근 영업일 기준)", 5, 30, 30)

token = get_access_token()
if token:
    df = get_investor_data(selected_ticker, token)
    
    if not df.empty:
        # 선택한 기간만큼만 자르기
        df = df.tail(period).copy()
        
        # 누적 데이터 계산
        df['외국인_누적'] = df['외국인_일일'].cumsum()
        df['기관_누적'] = df['기관_일일'].cumsum()
        
        # 그래프 시각화
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df['외국인_누적'], name='외국인 누적', line=dict(color='blue', width=3)))
        fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적'], name='기관 누적', line=dict(color='orange', width=3)))
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        
        fig.update_layout(title=f"{selected_name} 수급 누적 추세", hovermode="x unified", height=500)
        st.plotly_chart(fig, use_container_width=True)
        
        # 하단 데이터 표 (일일 순매수 + 누적 순매수)
        st.subheader("📋 수급 상세 내역 (일일 및 누적)")
        
        # 출력용 데이터프레임 정리 (가독성을 위해 최신일순 정렬)
        display_df = df[['외국인_일일', '기관_일일', '외국인_누적', '기관_누적']].tail(10).iloc[::-1]
        
        # 천 단위 쉼표 포맷팅 적용
        formatted_df = display_df.style.format("{:,.0f}")
        
        st.dataframe(formatted_df, use_container_width=True)
    else:
        st.warning("데이터를 불러올 수 없습니다.")
