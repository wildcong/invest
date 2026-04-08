import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ==========================================
# 🚨 API 키 설정 (보안 주의!)
# ==========================================
# 테스트 후에는 반드시 깃허브 공개(Public) 저장소에 올리지 마시고,
# Streamlit Secrets 기능을 이용해 숨기셔야 합니다.
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"

# ==========================================
# KOSPI 200 주요 종목 리스트 (에러 방지용 내장 데이터)
# ==========================================
KOSPI_DICT = {
    '삼성전자': '005930', 'SK하이닉스': '000660', 'LG에너지솔루션': '373220',
    '삼성바이오로직스': '207940', '현대차': '005380', '기아': '000270',
    '셀트리온': '068270', 'POSCO홀딩스': '005490', 'NAVER': '035420',
    '카카오': '035720', '삼성SDI': '006400', 'LG화학': '051910',
    '삼성물산': '028260', 'KB금융': '105560', '신한지주': '055550',
    '포스코퓨처엠': '003670', '현대모비스': '012330', '하나금융지주': '086790',
    'LG전자': '066570', '메리츠금융지주': '138040', '삼성생명': '032830',
    'SK': '034730', '카카오뱅크': '323410', '한국전력': '015760',
    'HD현대중공업': '329180', '삼성화재': '000810', 'HMM': '011200',
    'KT&G': '033780', '우리금융지주': '316140', '기업은행': '024110'
}

# ==========================================
# 1. API 접근 토큰 발급 함수
# ==========================================
@st.cache_data(show_spinner=False, ttl=86000) # 하루에 한 번만 발급
def get_access_token():
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body))
    if res.status_code == 200:
        return res.json().get("access_token")
    else:
        st.error(f"토큰 발급 실패: {res.text}")
        return None

# ==========================================
# 2. 투자자별 수급 데이터 가져오기 (최근 30일)
# ==========================================
@st.cache_data(show_spinner=False, ttl=3600) # 1시간마다 갱신
def get_investor_data(ticker, access_token):
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010900", # 주식 종목별 투자자 API
        "custtype": "P"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
    }
    res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor", headers=headers, params=params)
    
    if res.status_code == 200:
        data = res.json()
        df = pd.DataFrame(data['output'])
        if df.empty:
            return pd.DataFrame()
            
        # 필요한 칼럼: 날짜, 외국인 순매수(수량), 기관 순매수(수량)
        df = df[['stck_bsop_date', 'frgn_ntby_qty', 'orgn_ntby_qty']].copy()
        df.columns = ['Date', '외국인', '기관합계']
        
        # 데이터 타입 변환 및 정렬 (과거 -> 현재)
        df['Date'] = pd.to_datetime(df['Date'])
        df['외국인'] = pd.to_numeric(df['외국인'])
        df['기관합계'] = pd.to_numeric(df['기관합계'])
        df = df.sort_values('Date').set_index('Date')
        return df
    return pd.DataFrame()

# ==========================================
# 앱 UI 및 시각화 로직
# ==========================================
st.set_page_config(page_title="단기 수급 모멘텀 분석기", layout="wide")

st.title("📊 KOSPI 단기 수급 쌍끌이 분석 (KIS API)")
st.markdown("한국투자증권 API를 활용하여 **최근 30영업일 간의 외국인/기관 수급 변곡점**을 정확하게 포착합니다.")

# 사이드바
st.sidebar.header("설정 (Settings)")
selected_name = st.sidebar.selectbox("종목을 선택하세요", list(KOSPI_DICT.keys()))
selected_ticker = KOSPI_DICT[selected_name]

# 30일치 데이터이므로 이동평균을 짧게 가져갑니다.
ma_window = st.sidebar.slider("추세선 평활화 기간 (일)", min_value=2, max_value=15, value=3, step=1, help="숫자가 작을수록 최근 단기 방향성에 민감하게 반응합니다.")

if st.sidebar.button("데이터 조회"):
    with st.spinner(f"{selected_name} 수급 데이터를 한투 API에서 가져오는 중..."):
        
        # 1. 토큰 발급
        token = get_access_token()
        if not token:
            st.stop()
            
        # 2. 데이터 조회
        raw_df = get_investor_data(selected_ticker, token)
        
        if raw_df.empty:
            st.error("데이터를 가져오지 못했습니다. API 키 오류이거나 한투 서버 점검 시간일 수 있습니다.")
        else:
            df = raw_df.copy()
            
            # 3. 데이터 가공 (누적 순매수 및 추세 계산)
            df['외국인_누적'] = df['외국인'].cumsum()
            df['기관_누적'] = df['기관합계'].cumsum()
            
            df['외국인_이평'] = df['외국인_누적'].rolling(window=ma_window).mean()
            df['기관_이평'] = df['기관_누적'].rolling(window=ma_window).mean()
            
            df['외국인_기울기'] = df['외국인_이평'].diff()
            df['기관_기울기'] = df['기관_이평'].diff()
            
            # 방향성 판단
            conditions = [
                (df['외국인_기울기'] > 0) & (df['기관_기울기'] > 0),
                (df['외국인_기울기'] < 0) & (df['기관_기울기'] < 0)
            ]
            choices = [1, -1]
            df['동반방향'] = np.select(conditions, choices, default=0)
            df = df.dropna()
            
            # 4. 시각화 (Plotly)
            fig = make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                row_heights=[0.7, 0.3], vertical_spacing=0.05,
                subplot_titles=(f"{selected_name} 누적 순매수 추세 (최근 30일 / {ma_window}일 이평)", 
                                "외국인/기관 동반 매수/매도 상태")
            )
            
            # 위 차트: 선 그래프
            fig.add_trace(go.Scatter(x=df.index, y=df['외국인_이평'], mode='lines', name='외국인 추세', line=dict(color='blue', width=2)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df['기관_이평'], mode='lines', name='기관 추세', line=dict(color='orange', width=2)), row=1, col=1)
            
            # 아래 차트: 막대 그래프 (색상 로직)
            colors = ['rgba(0, 128, 0, 0.7)' if val == 1 else 'rgba(255, 0, 0, 0.7)' if val == -1 else 'rgba(200, 200, 200, 0.3)' for val in df['동반방향']]
            
            fig.add_trace(go.Bar(x=[df.index[0]], y=[0], marker_color='rgba(0, 128, 0, 0.7)', name='쌍끌이 매수 (+, +)'), row=2, col=1)
            fig.add_trace(go.Bar(x=[df.index[0]], y=[0], marker_color='rgba(255, 0, 0, 0.7)', name='쌍끌이 매도 (-, -)'), row=2, col=1)
            fig.add_trace(go.Bar(x=df.index, y=df['동반방향'], marker_color=colors, showlegend=False), row=2, col=1)
            
            fig.update_layout(height=600, hovermode='x unified', margin=dict(l=20, r=20, t=60, b=20))
            fig.update_yaxes(title_text="누적 순매수 수량", row=1, col=1)
            fig.update_yaxes(title_text="일치 방향성", tickvals=[-1, 0, 1], ticktext=["동반매도", "엇갈림", "동반매수"], range=[-1.2, 1.2], row=2, col=1)
            
            st.plotly_chart(fig, use_container_width=True)
            
            # 5. 요약 데이터 표시
            st.subheader("📊 최근 5일 동반 수급 현황")
            display_df = df[['외국인', '기관합계', '동반방향']].tail(5).iloc[::-1]
            display_df.columns = ['외국인 일일순매수', '기관 일일순매수', '쌍끌이 상태 (1:매수, -1:매도)']
            st.dataframe(display_df, use_container_width=True)
