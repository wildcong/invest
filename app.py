import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
import FinanceDataReader as fdr

# ==========================================
# 🔒 보안 설정 (Streamlit Secrets)
# ==========================================
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"

st.set_page_config(page_title="KOSPI 200 누적 수급 분석", layout="wide")

# ==========================================
# 1. KOSPI 시총 상위 200 종목 가져오기 (컬럼명 에러 완벽 해결)
# ==========================================
@st.cache_data(ttl=86400)
def get_kospi200_list():
    try:
        df_kospi = fdr.StockListing('KOSPI')
        # 버전에 따른 대소문자 차이 방어 코드
        if 'Marcap' in df_kospi.columns:
            df_200 = df_kospi.sort_values('Marcap', ascending=False).head(200)
        elif 'MarCap' in df_kospi.columns:
            df_200 = df_kospi.sort_values('MarCap', ascending=False).head(200)
        else:
            df_200 = df_kospi.head(200) # 컬럼이 없으면 기본 정렬된 상위 200개
            
        return dict(zip(df_200['Name'], df_200['Code']))
    except Exception as e:
        st.error(f"종목 리스트 로딩 에러: {e}")
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
        df.columns = ['Date', '외국인', '기관']
        df['Date'] = pd.to_datetime(df['Date'])
        for col in ['외국인', '기관']:
            df[col] = pd.to_numeric(df[col])
            
        return df.sort_values('Date').set_index('Date')
    return pd.DataFrame()

# ==========================================
# 3. 화면 UI 및 버튼 없는 자동 렌더링
# ==========================================
st.title("📈 KOSPI 시총 200 누적 수급 분석기")
st.markdown("한국투자증권 API를 활용하여 클라우드 환경에서도 막힘없이 **최근 30일 누적 수급**을 보여줍니다.")

with st.spinner("KOSPI 200 종목 리스트를 가져오는 중입니다..."):
    kospi_dict = get_kospi200_list()

st.sidebar.header("종목 설정")
# 드롭다운에서 종목을 바꾸는 순간 즉시 아래 코드가 재실행됩니다.
selected_name = st.sidebar.selectbox("종목 선택 (시총 상위 200)", list(kospi_dict.keys()))
selected_ticker = kospi_dict[selected_name]

# 데이터 수집 및 분석 (버튼 조건문 제거)
token = get_access_token()

if not token:
    st.error("API 토큰 발급에 실패했습니다. 키 설정을 다시 확인해 주세요.")
    st.stop()

with st.spinner(f"{selected_name} 데이터를 한투 API에서 불러오는 중..."):
    df = get_investor_data(selected_ticker, token)
    
    if df.empty:
        st.error("데이터를 가져오지 못했습니다. 장 점검 시간이거나 종목 코드 오류일 수 있습니다.")
    else:
        # 1. 일일 순매수를 '누적'으로 합산
        df['외국인_누적'] = df['외국인'].cumsum()
        df['기관_누적'] = df['기관'].cumsum()
        
        # 2. Plotly 시각화
        fig = go.Figure()
        
        # 누적 곡선 그리기
        fig.add_trace(go.Scatter(x=df.index, y=df['외국인_누적'], mode='lines+markers', name='외국인 누적', line=dict(color='blue', width=3)))
        fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적'], mode='lines+markers', name='기관 누적', line=dict(color='orange', width=3)))
        
        # 0점 기준선 (플러스/마이너스 전환점)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        
        fig.update_layout(
            title=f"<b>{selected_name}</b> 최근 30일 누적 수급 추이",
            hovermode="x unified",
            height=600,
            xaxis_title="날짜",
            yaxis_title="누적 순매수 수량 (주)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # 3. 요약 데이터 표시
        st.write("### 📊 최근 5거래일 누적 데이터")
        # 보기 편하게 최신 날짜가 위로 오게 뒤집어서 출력
        st.table(df[['외국인_누적', '기관_누적']].tail(5).iloc[::-1])
