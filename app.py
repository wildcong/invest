import os

# ==========================================
# 🛡️ 프록시(Proxy) 설정: KRX IP 차단 우회
# ==========================================
# 주의: 깃허브 공개(Public) 저장소에 코드를 올리실 경우,
# 아이디(warmhoon)와 비밀번호(5741oo)가 노출되니 각별히 주의하세요!
proxy_url = "http://warmhoon:5741oo@152.67.216.179:3128"

# 파이썬 내부의 모든 웹 요청(requests)이 이 프록시를 거치도록 강제 설정
os.environ['HTTP_PROXY'] = proxy_url
os.environ['HTTPS_PROXY'] = proxy_url
os.environ['http_proxy'] = proxy_url
os.environ['https_proxy'] = proxy_url
# ==========================================

import streamlit as st
import pandas as pd
import numpy as np
from pykrx import stock
import pykrx.stock.stock_api as stock_api
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, timezone

# --- 이전과 동일한 KST 시간대 방어 코드 ---
def safe_business_day(date=None, prev=False):
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    if now.hour < 16:
        now -= timedelta(days=1)
    if now.weekday() == 5:   
        now -= timedelta(days=1)
    elif now.weekday() == 6: 
        now -= timedelta(days=2)
    return now.strftime("%Y%m%d")

stock_api.get_nearest_business_day_in_a_week = safe_business_day

# ---------------------------------------------------------
# 이하 코드는 직전에 만들어드린 '절대 꺼지지 않는 최후 방어 코드'와
# 100% 동일하게 붙여넣으시면 됩니다.
# ---------------------------------------------------------

st.set_page_config(page_title="수급 쌍끌이 분석기", layout="wide")

@st.cache_data(show_spinner=False, ttl=43200)
def get_kospi200_stocks():
    # ... (기존 코드 생략 - 직전 답변의 코드 사용) ...
import os

# ==========================================
# 🛡️ 프록시(Proxy) 설정: KRX IP 차단 우회
# ==========================================
# 주의: 깃허브 공개(Public) 저장소에 코드를 올리실 경우,
# 아이디(warmhoon)와 비밀번호(5741oo)가 노출되니 각별히 주의하세요!
proxy_url = "http://warmhoon:5741oo@152.67.216.179:3128"

# 파이썬 내부의 모든 웹 요청(requests)이 이 프록시를 거치도록 강제 설정
os.environ['HTTP_PROXY'] = proxy_url
os.environ['HTTPS_PROXY'] = proxy_url
os.environ['http_proxy'] = proxy_url
os.environ['https_proxy'] = proxy_url
# ==========================================

import streamlit as st
import pandas as pd
import numpy as np
from pykrx import stock
import pykrx.stock.stock_api as stock_api
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, timezone

# --- 이전과 동일한 KST 시간대 방어 코드 ---
def safe_business_day(date=None, prev=False):
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    if now.hour < 16:
        now -= timedelta(days=1)
    if now.weekday() == 5:   
        now -= timedelta(days=1)
    elif now.weekday() == 6: 
        now -= timedelta(days=2)
    return now.strftime("%Y%m%d")

stock_api.get_nearest_business_day_in_a_week = safe_business_day

# ---------------------------------------------------------
# 이하 코드는 직전에 만들어드린 '절대 꺼지지 않는 최후 방어 코드'와
# 100% 동일하게 붙여넣으시면 됩니다.
# ---------------------------------------------------------

st.set_page_config(page_title="수급 쌍끌이 분석기", layout="wide")

@st.cache_data(show_spinner=False, ttl=43200)
def get_kospi200_stocks():
    # 최악의 경우(KRX IP 차단 등)를 대비한 코스피 핵심 20종목 예비 리스트
    fallback_dict = {
        '삼성전자': '005930', 'SK하이닉스': '000660', 'LG에너지솔루션': '373220',
        '삼성바이오로직스': '207940', '현대차': '005380', '기아': '000270',
        '셀트리온': '068270', 'POSCO홀딩스': '005490', 'NAVER': '035420',
        '카카오': '035720', '삼성SDI': '006400', 'LG화학': '051910',
        '삼성물산': '028260', 'KB금융': '105560', '신한지주': '055550',
        '포스코퓨처엠': '003670', '현대모비스': '012330', '하나금융지주': '086790',
        'LG전자': '066570', '메리츠금융지주': '138040'
    }
    
    try:
        tickers = stock.get_index_portfolio_deposit_file("1028")
        if not tickers: # 리스트를 받아왔는데 텅 비어있다면 에러 유발
            raise ValueError("KRX returned empty list")
            
        stock_dict = {}
        for ticker in tickers:
            name = stock.get_market_ticker_name(ticker)
            stock_dict[name] = ticker
        return stock_dict
        
    except Exception as e:
        # 에러가 나면 화면에 작게 경고를 띄우고 예비 리스트 반환 (앱 크래시 방지)
        return fallback_dict

# 2. 투자자별 수급 데이터 가져오기 (방어 코드)
@st.cache_data(show_spinner=False)
def get_investor_data(ticker, start_date, end_date):
    try:
        df = stock.get_market_trading_value_by_date(start_date, end_date, ticker)
        return df
    except Exception:
        return pd.DataFrame()

st.title("📊 KOSPI 200 외국인 & 기관 수급 쌍끌이 분석")
st.markdown("선택한 종목의 외국인과 기관 누적 순매수 **추세(기울기)가 동시에 양(+)이거나 음(-)인 구간**을 중점적으로 확인합니다.")

# 데이터 로딩
kospi200_dict = get_kospi200_stocks()

# KOSPI 종목을 하나도 못 가져왔을 때의 최후 방어선
if not kospi200_dict:
    st.error("종목 데이터를 초기화할 수 없습니다. 서버 통신에 완전히 실패했습니다.")
    st.stop()

st.sidebar.header("설정 (Settings)")

# 사이드바 렌더링
selected_name = st.sidebar.selectbox("종목을 선택하세요", list(kospi200_dict.keys()))

# 선택된 값이 없을 때 방어
if not selected_name:
    st.warning("종목이 선택되지 않았습니다.")
    st.stop()

selected_ticker = kospi200_dict[selected_name]

# 시간 설정 (KST)
KST = timezone(timedelta(hours=9))
end_date_default = datetime.now(KST).date()
start_date_default = end_date_default - timedelta(days=365)

start_date = st.sidebar.date_input("시작일", start_date_default)
end_date = st.sidebar.date_input("종료일", end_date_default)

ma_window = st.sidebar.slider("추세선 평활화 기간 (일)", min_value=5, max_value=60, value=10, step=1)

if st.sidebar.button("데이터 조회"):
    with st.spinner(f"{selected_name} 데이터를 분석 중입니다..."):
        str_start = start_date.strftime("%Y%m%d")
        str_end = end_date.strftime("%Y%m%d")
        
        raw_df = get_investor_data(selected_ticker, str_start, str_end)
        
        if raw_df.empty:
            st.error("🚨 해당 기간의 데이터가 없거나, KRX 서버가 클라우드 IP를 차단하여 데이터를 가져오지 못했습니다. 로컬 PC에서 실행해 보세요.")
        elif '외국인' not in raw_df.columns or '기관합계' not in raw_df.columns:
            st.error("데이터 구조가 올바르지 않습니다. (외국인/기관 데이터 누락)")
        else:
            df = raw_df[['외국인', '기관합계']].copy()
            
            df['외국인_누적'] = df['외국인'].cumsum()
            df['기관_누적'] = df['기관합계'].cumsum()
            
            df['외국인_이평'] = df['외국인_누적'].rolling(window=ma_window).mean()
            df['기관_이평'] = df['기관_누적'].rolling(window=ma_window).mean()
            
            df['외국인_기울기'] = df['외국인_이평'].diff()
            df['기관_기울기'] = df['기관_이평'].diff()
            
            conditions = [
                (df['외국인_기울기'] > 0) & (df['기관_기울기'] > 0),
                (df['외국인_기울기'] < 0) & (df['기관_기울기'] < 0)
            ]
            choices = [1, -1]
            df['동반방향'] = np.select(conditions, choices, default=0)
            df = df.dropna()
            
            fig = make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                row_heights=[0.7, 0.3], vertical_spacing=0.05,
                subplot_titles=(f"{selected_name} ({selected_ticker}) 누적 순매수 추세 ({ma_window}일 이평)", 
                                "외국인/기관 동반 매수/매도 상태")
            )
            
            fig.add_trace(go.Scatter(x=df.index, y=df['외국인_이평'], mode='lines', name='외국인 추세', line=dict(color='blue', width=2)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df['기관_이평'], mode='lines', name='기관 추세', line=dict(color='orange', width=2)), row=1, col=1)
            
            colors = ['rgba(0, 128, 0, 0.7)' if val == 1 else 'rgba(255, 0, 0, 0.7)' if val == -1 else 'rgba(200, 200, 200, 0.3)' for val in df['동반방향']]
            
            fig.add_trace(go.Bar(x=[df.index[0]], y=[0], marker_color='rgba(0, 128, 0, 0.7)', name='쌍끌이 매수 (+, +)'), row=2, col=1)
            fig.add_trace(go.Bar(x=[df.index[0]], y=[0], marker_color='rgba(255, 0, 0, 0.7)', name='쌍끌이 매도 (-, -)'), row=2, col=1)
            fig.add_trace(go.Bar(x=df.index, y=df['동반방향'], marker_color=colors, showlegend=False), row=2, col=1)
            
            fig.update_layout(height=600, hovermode='x unified', margin=dict(l=20, r=20, t=60, b=20))
            fig.update_yaxes(title_text="누적 순매수 금액", row=1, col=1)
            fig.update_yaxes(title_text="일치 방향성", tickvals=[-1, 0, 1], ticktext=["동반매도", "엇갈림", "동반매수"], range=[-1.2, 1.2], row=2, col=1)
            
            st.plotly_chart(fig, use_container_width=True)
