import streamlit as st
import pandas as pd
import numpy as np
from pykrx import stock
import pykrx.stock.stock_api as stock_api
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, timezone

# ==========================================
# 🛠️ pykrx IndexError 버그 완벽 해결 (KST 시간대 적용)
# ==========================================
def safe_business_day(date=None, prev=False):
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    
    if now.hour < 16:
        now -= timedelta(days=1)
        
    if now.weekday() == 5:   # 토요일
        now -= timedelta(days=1)
    elif now.weekday() == 6: # 일요일
        now -= timedelta(days=2)
        
    return now.strftime("%Y%m%d")

stock_api.get_nearest_business_day_in_a_week = safe_business_day
# ==========================================

# 페이지 기본 설정
st.set_page_config(page_title="수급 방향성 분석기", layout="wide")

# 1. KOSPI 200 종목 정보 가져오기 (12시간마다 알아서 캐시 갱신)
@st.cache_data(show_spinner=False, ttl=43200)
def get_kospi200_stocks():
    try:
        tickers = stock.get_index_portfolio_deposit_file("1028")
        stock_dict = {}
        for ticker in tickers:
            name = stock.get_market_ticker_name(ticker)
            stock_dict[name] = ticker
        return stock_dict
    except Exception:
        return {} # 에러 발생 시 빈 딕셔너리 반환

# 2. 투자자별 순매수 데이터 가져오기
@st.cache_data(show_spinner=False)
def get_investor_data(ticker, start_date, end_date):
    try:
        df = stock.get_market_trading_value_by_date(start_date, end_date, ticker)
        return df
    except Exception:
        return pd.DataFrame()

st.title("📊 KOSPI 200 외국인 & 기관 수급 쌍끌이 분석")
st.markdown("선택한 종목의 외국인과 기관 누적 순매수 **추세(기울기)가 동시에 양(+)이거나 음(-)인 구간**을 중점적으로 확인합니다.")

with st.spinner("KOSPI 200 종목 목록을 불러오는 중입니다..."):
    kospi200_dict = get_kospi200_stocks()

# 🚨 방어 코드 1: 종목 목록을 못 가져왔을 때 앱 크래시 방지
if not kospi200_dict:
    st.error("⚠️ 한국거래소(KRX) 서버에서 KOSPI 200 목록을 가져오지 못했습니다. 우측 상단 메뉴(⋮)에서 'Clear cache'를 클릭하시거나 잠시 후 다시 시도해 주세요.")
    st.stop() # 여기서 앱 실행을 안전하게 중단

# 사이드바 설정 (종목, 기간, 이동평균 설정)
st.sidebar.header("설정 (Settings)")

# 종목 선택 드롭다운
selected_name = st.sidebar.selectbox("KOSPI 200 종목을 선택하세요", list(kospi200_dict.keys()))

# 🚨 방어 코드 2: 선택된 종목이 없을 때 크래시 방지
if not selected_name:
    st.warning("종목을 선택해 주세요.")
    st.stop()

selected_ticker = kospi200_dict[selected_name]

# 날짜 선택 (한국 시간 KST 기준)
KST = timezone(timedelta(hours=9))
end_date_default = datetime.now(KST).date()
start_date_default = end_date_default - timedelta(days=365)

start_date = st.sidebar.date_input("시작일", start_date_default)
end_date = st.sidebar.date_input("종료일", end_date_default)

ma_window = st.sidebar.slider("추세선 평활화 기간 (일)", min_value=5, max_value=60, value=10, step=1)

if st.sidebar.button("데이터 조회"):
    with st.spinner("데이터를 분석 중입니다..."):
        str_start = start_date.strftime("%Y%m%d")
        str_end = end_date.strftime("%Y%m%d")
        
        raw_df = get_investor_data(selected_ticker, str_start, str_end)
        
        if raw_df.empty:
            st.error("해당 기간의 데이터가 없거나 서버 통신에 실패했습니다.")
        else:
            # 필수 칼럼이 있는지 확인
            if '외국인' not in raw_df.columns or '기관합계' not in raw_df.columns:
                st.error("요청하신 데이터에 외국인/기관 수급 정보가 포함되어 있지 않습니다.")
                st.stop()
                
            df = raw_df[['외국인', '기관합계']].copy()
            
            # 누적 및 이동평균 계산
            df['외국인_누적'] = df['외국인'].cumsum()
            df['기관_누적'] = df['기관합계'].cumsum()
            
            df['외국인_이평'] = df['외국인_누적'].rolling(window=ma_window).mean()
            df['기관_이평'] = df['기관_누적'].rolling(window=ma_window).mean()
            
            df['외국인_기울기'] = df['외국인_이평'].diff()
            df['기관_기울기'] = df['기관_이평'].diff()
            
            # 방향성 판단 (동반매수: 1, 동반매도: -1, 엇갈림: 0)
            conditions = [
                (df['외국인_기울기'] > 0) & (df['기관_기울기'] > 0),
                (df['외국인_기울기'] < 0) & (df['기관_기울기'] < 0)
            ]
            choices = [1, -1]
            df['동반방향'] = np.select(conditions, choices, default=0)
            
            df = df.dropna()
            
            # 시각화 (Plotly)
            fig = make_subplots(
                rows=2, cols=1, 
                shared_xaxes=True,
                row_heights=[0.7, 0.3], 
                vertical_spacing=0.05,
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
            
            st.subheader("최근 동반 수급 상세 데이터")
            display_df = df[['외국인_기울기', '기관_기울기', '동반방향']].tail(10).iloc[::-1]
            display_df.columns = ['외국인 추세(기울기)', '기관 추세(기울기)', '쌍끌이 상태 (1:매수, -1:매도)']
            st.dataframe(display_df, use_container_width=True)
