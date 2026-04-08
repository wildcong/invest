import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import FinanceDataReader as fdr  # 종목 리스트 확보용

# ==========================================
# 🔒 보안 설정 (Streamlit Secrets)
# ==========================================
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"

# ==========================================
# 1. KOSPI 200 전 종목 리스트 가져오기 (시총 순)
# ==========================================
@st.cache_data(ttl=86400) # 하루에 한 번만 로드
def get_kospi200_list():
    try:
        # KOSPI 종목 전체를 가져와서 시가총액 순으로 정렬된 데이터를 활용
        df_kospi = fdr.StockListing('KOSPI')
        # KOSPI 200 종목만 필터링 (라이브러리 버전에 따라 다를 수 있어 상위 200개 활용 권장)
        # 실제 KOSPI 200 구성 종목과 일치시키기 위해 정렬 후 상위 200개를 선택합니다.
        df_200 = df_kospi.sort_values('MarCap', ascending=False).head(200)
        return dict(zip(df_200['Name'], df_200['Code']))
    except:
        return {"삼성전자": "005930", "SK하이닉스": "000660"} # 예외 발생 시 기본값

# ==========================================
# 2. 한국투자증권 토큰 및 데이터 수집 함수
# ==========================================
@st.cache_data(ttl=86000)
def get_access_token():
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body))
    return res.json().get("access_token") if res.status_code == 200 else None

@st.cache_data(ttl=3600)
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
        df = df[['stck_bsop_date', 'frgn_ntby_qty', 'orgn_ntby_qty']].copy()
        df.columns = ['Date', '외국인', '기관']
        df['Date'] = pd.to_datetime(df['Date'])
        # 문자열 데이터를 숫자로 변환
        for col in ['외국인', '기관']:
            df[col] = pd.to_numeric(df[col])
        return df.sort_values('Date').set_index('Date')
    return pd.DataFrame()

# ==========================================
# 3. Streamlit UI 및 누적 계산 로직
# ==========================================
st.set_page_config(page_title="KOSPI 200 누적 수급 분석", layout="wide")
st.title("📈 KOSPI 200 시총 상위 200개 종목 누적 수급")

with st.spinner("종목 리스트를 불러오는 중..."):
    kospi_dict = get_kospi200_list()

selected_name = st.sidebar.selectbox("종목 선택 (시총 순)", list(kospi_dict.keys()))
selected_ticker = kospi_dict[selected_name]

if st.sidebar.button("데이터 분석 시작"):
    token = get_access_token()
    df = get_investor_data(selected_ticker, token)
    
    if not df.empty:
        # 핵심: 일일 순매수 데이터를 누적으로 합산 (Cumulative Sum)
        df['외국인_누적'] = df['외국인'].cumsum()
        df['기관_누적'] = df['기관'].cumsum()
        
        # 차트 생성
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df['외국인_누적'], name='외국인 누적 매수', line=dict(color='blue')))
        fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적'], name='기관 누적 매수', line=dict(color='orange')))
        
        fig.update_layout(title=f"{selected_name} 수급 누적 추세", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        
        st.write("### 최근 30일 누적 수치 요약")
        st.table(df[['외국인_누적', '기관_누적']].tail(5).iloc[::-1])
