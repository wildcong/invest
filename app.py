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

st.set_page_config(page_title="KOSPI 200 수급 금액 및 주가 분석", layout="wide")

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
st.title("📈 KOSPI 200 주가 및 자금(억원) 흐름 분석")
st.markdown("수량(주) 대신 **순매수 금액(억원)** 기준으로 환산하여 자금의 흐름을 봅니다.")

with st.spinner("종목 리스트 로딩 중..."):
    kospi_dict = get_kospi200_list()
    kospi_names = list(kospi_dict.keys())

# ==========================================
# 4. 사이드바: 완벽하게 작동하는 콜백(Callback) 네비게이션
# ==========================================
st.sidebar.header("설정")

# 세션 상태 초기화
if 'current_idx' not in st.session_state:
    st.session_state.current_idx = 0
if 'stock_selector' not in st.session_state:
    st.session_state.stock_selector = kospi_names[0]

# 콜백 함수: 드롭다운을 마우스로 바꿨을 때
def update_from_selectbox():
    st.session_state.current_idx = kospi_names.index(st.session_state.stock_selector)

# 콜백 함수: 이전 버튼을 눌렀을 때
def go_prev():
    if st.session_state.current_idx > 0:
        st.session_state.current_idx -= 1
        st.session_state.stock_selector = kospi_names[st.session_state.current_idx]

# 콜백 함수: 다음 버튼을 눌렀을 때
def go_next():
    if st.session_state.current_idx < len(kospi_names) - 1:
        st.session_state.current_idx += 1
        st.session_state.stock_selector = kospi_names[st.session_state.current_idx]

# 버튼 렌더링 (클릭 시 콜백 함수 즉시 실행)
col1, col2 = st.sidebar.columns(2)
with col1:
    st.button("⬅️ 이전 종목", on_click=go_prev, use_container_width=True)
with col2:
    st.button("다음 종목 ➡️", on_click=go_next, use_container_width=True)

# 드롭다운
selected_name = st.sidebar.selectbox(
    "종목 선택 (시총 상위 200)", 
    kospi_names, 
    key="stock_selector",
    on_change=update_from_selectbox
)

selected_ticker = kospi_dict[selected_name]
period = st.sidebar.slider("분석 기간 (최대 30영업일)", 5, 30, 30)

# ==========================================
# 5. 데이터 분석 및 시각화 로직
# ==========================================
token = get_access_token()
if token:
    df = get_investor_data(selected_ticker, token)
    
    if not df.empty:
        df = df.tail(period).copy()
        
        # 금액(억원)으로 환산 로직
        df['외국인_일일(억원)'] = (df['외국인_일일(주)'] * df['주가(원)']) / 100000000
        df['기관_일일(억원)'] = (df['기관_일일(주)'] * df['주가(원)']) / 100000000
        
        # 누적 금액 계산
        df['외국인_누적(억원)'] = df['외국인_일일(억원)'].cumsum()
        df['기관_누적(억원)'] = df['기관_일일(억원)'].cumsum()
        
        # 그래프 생성 (이중 축)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        
        # 누적 금액 곡선
        fig.add_trace(go.Scatter(x=df.index, y=df['외국인_누적(억원)'], name='외국인 누적 (억원)', line=dict(color='blue', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적(억원)'], name='기관 누적 (억원)', line=dict(color='orange', width=3)), secondary_y=False)
        
        # 주가 곡선
        fig.add_trace(go.Scatter(x=df.index, y=df['주가(원)'], name='주가 (원)', line=dict(color='red', width=2, dash='dot')), secondary_y=True)
        
        # 0점 기준선
        fig.add_hline(y=0, line_dash="dash", line_color="gray", secondary_y=False)
        
        fig.update_layout(
            title=f"<b>{selected_name}</b> 주가 및 누적 자금 유입량(억원) 비교", 
            hovermode="x unified", 
            height=600,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        fig.update_yaxes(title_text="누적 순매수 금액 (억원)", secondary_y=False)
        fig.update_yaxes(title_text="<b>주가 (원)</b>", secondary_y=True, showgrid=False)
        
        st.plotly_chart(fig, use_container_width=True)
        
        # ==========================================
        # 6. 하단 데이터 표 (금액 단위)
        # ==========================================
        st.subheader(f"📋 자금 동향 상세 내역 (최근 {period}일)")
        
        display_df = df[['주가(원)', '외국인_일일(억원)', '기관_일일(억원)', '외국인_누적(억원)', '기관_누적(억원)']].iloc[::-1]
        
        formatted_df = display_df.style.format({
            "주가(원)": "{:,.0f}",
            "외국인_일일(억원)": "{:,.1f}",
            "기관_일일(억원)": "{:,.1f}",
            "외국인_누적(억원)": "{:,.1f}",
            "기관_누적(억원)": "{:,.1f}"
        })
        
        st.dataframe(formatted_df, use_container_width=True)
    else:
        st.warning("데이터를 불러올 수 없습니다.")
