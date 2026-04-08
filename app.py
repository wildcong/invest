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

st.set_page_config(page_title="KOSPI 200 수급 및 주가 분석", layout="wide")

# ==========================================
# 1. KOSPI 200 종목 리스트 가져오기
# ==========================================
@st.cache_data(ttl=86400)
def get_kospi200_list():
    try:
        df_kospi = fdr.StockListing('KOSPI')
        mcap_col = 'Marcap' if 'Marcap' in df_kospi.columns else 'MarCap'
        df_200 = df_kospi.sort_values(mcap_col, ascending=False).head(200)
        return dict(zip(df_200['Name'], df_200['Code']))
    except Exception as e:
        return {"삼성전자": "005930", "SK하이닉스": "000660"}

# ==========================================
# 2. 한국투자증권 API (주가 및 수급 동시 수집)
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
        
        # stck_clpr(종가) 칼럼 추가 추출!
        df = df[['stck_bsop_date', 'stck_clpr', 'frgn_ntby_qty', 'orgn_ntby_qty']].copy()
        df.columns = ['Date', '주가(원)', '외국인_일일(주)', '기관_일일(주)']
        
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['주가(원)', '외국인_일일(주)', '기관_일일(주)']:
            df[col] = pd.to_numeric(df[col])
            
        return df.sort_values('Date').set_index('Date')
    return pd.DataFrame()

# ==========================================
# 3. 메인 화면 구성
# ==========================================
st.title("📈 KOSPI 200 주가 및 수급 분석 (최근 30일)")
st.markdown("한국투자증권 API 정책상 **최대 30영업일** 데이터만 제공됩니다. 주가 흐름과 누적 수급(수량)을 함께 비교해 보세요.")

with st.spinner("종목 리스트 로딩 중..."):
    kospi_dict = get_kospi200_list()

st.sidebar.header("설정")
selected_name = st.sidebar.selectbox("종목 선택", list(kospi_dict.keys()))
selected_ticker = kospi_dict[selected_name]

# 30일 내에서 기간 조절
period = st.sidebar.slider("분석 기간 (영업일)", 5, 30, 30)

token = get_access_token()
if token:
    df = get_investor_data(selected_ticker, token)
    
    if not df.empty:
        df = df.tail(period).copy()
        
        # 수량 기준 누적 데이터 계산
        df['외국인_누적(주)'] = df['외국인_일일(주)'].cumsum()
        df['기관_누적(주)'] = df['기관_일일(주)'].cumsum()
        
        # ==========================================
        # 📊 이중 축(Dual Axis) 그래프 생성
        # ==========================================
        # secondary_y=True 를 설정하여 좌/우 축을 분리합니다.
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        
        # 누적 수급 곡선 (왼쪽 Y축 사용)
        fig.add_trace(go.Scatter(x=df.index, y=df['외국인_누적(주)'], name='외국인 누적 (주)', line=dict(color='blue', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적(주)'], name='기관 누적 (주)', line=dict(color='orange', width=3)), secondary_y=False)
        
        # 주가 곡선 (오른쪽 Y축 사용, 점선 처리)
        fig.add_trace(go.Scatter(x=df.index, y=df['주가(원)'], name='주가 (원)', line=dict(color='red', width=2, dash='dot')), secondary_y=True)
        
        # 0점 기준선
        fig.add_hline(y=0, line_dash="dash", line_color="gray", secondary_y=False)
        
        # 차트 레이아웃 디자인
        fig.update_layout(
            title=f"<b>{selected_name}</b> 주가 및 누적 수급 비교", 
            hovermode="x unified", 
            height=600,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        # Y축 이름 설정
        fig.update_yaxes(title_text="누적 순매수 수량 (주)", secondary_y=False)
        fig.update_yaxes(title_text="<b>주가 (원)</b>", secondary_y=True, showgrid=False)
        
        st.plotly_chart(fig, use_container_width=True)
        
        # ==========================================
        # 📋 하단 데이터 표 (주가 + 일일 + 누적)
        # ==========================================
        st.subheader(f"📋 상세 데이터 내역 (최근 {period}일)")
        
        # 출력 순서 정렬 (최신 날짜가 위로 오게)
        display_df = df[['주가(원)', '외국인_일일(주)', '기관_일일(주)', '외국인_누적(주)', '기관_누적(주)']].iloc[::-1]
        
        # 보기 좋게 천 단위 쉼표 추가
        formatted_df = display_df.style.format("{:,.0f}")
        
        st.dataframe(formatted_df, use_container_width=True)
    else:
        st.warning("데이터를 불러올 수 없습니다.")
