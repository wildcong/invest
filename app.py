import streamlit as st
import pandas as pd
import numpy as np
from pykrx import stock
import pykrx.stock.stock_api as stock_api  # <- 패치를 위해 추가
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import FinanceDataReader as fdr


# ==========================================
# 🛠️ pykrx IndexError 버그 임시 해결 (Monkey Patch)
# ==========================================
def safe_business_day(date=None, prev=False):
    now = datetime.today()
    # 주말(토, 일)인 경우 가장 최근 영업일인 금요일로 날짜 강제 조정
    if now.weekday() == 5:   # 토요일
        now -= timedelta(days=1)
    elif now.weekday() == 6: # 일요일
        now -= timedelta(days=2)
    return now.strftime("%Y%m%d")

# pykrx 내부의 에러나는 함수를 강제로 안전한 함수로 교체
stock_api.get_nearest_business_day_in_a_week = safe_business_day
# ==========================================

# 페이지 기본 설정
st.set_page_config(page_title="수급 방향성 분석기", layout="wide")


# 기존 get_kospi200_stocks() 함수를 아래 내용으로 교체
@st.cache_data(show_spinner=False)
def get_kospi200_stocks():
    # FinanceDataReader를 사용하여 KOSPI 200 종목 가져오기
    df_kospi200 = fdr.StockListing('KOSPI200')
    
    stock_dict = {}
    # 코드(Symbol)와 종목명(Name)을 딕셔너리로 묶어줍니다
    for idx, row in df_kospi200.iterrows():
        stock_dict[row['Name']] = row['Symbol']
        
    return stock_dict

# 2. 투자자별 순매수 데이터 가져오기 (캐싱)
@st.cache_data(show_spinner=False)
def get_investor_data(ticker, start_date, end_date):
    df = stock.get_market_trading_value_by_date(start_date, end_date, ticker)
    return df

st.title("📊 KOSPI 200 외국인 & 기관 수급 쌍끌이 분석")
st.markdown("선택한 종목의 외국인과 기관 누적 순매수 **추세(기울기)가 동시에 양(+)이거나 음(-)인 구간**을 중점적으로 확인합니다.")

with st.spinner("KOSPI 200 종목 목록을 불러오는 중입니다..."):
    kospi200_dict = get_kospi200_stocks()

# 사이드바 설정 (종목, 기간, 이동평균 설정)
st.sidebar.header("설정 (Settings)")

# 종목 선택 드롭다운
selected_name = st.sidebar.selectbox("KOSPI 200 종목을 선택하세요", list(kospi200_dict.keys()))
selected_ticker = kospi200_dict[selected_name]

# 날짜 선택
end_date_default = datetime.today()
start_date_default = end_date_default - timedelta(days=365)
start_date = st.sidebar.date_input("시작일", start_date_default)
end_date = st.sidebar.date_input("종료일", end_date_default)

# 추세선(이동평균) 기간 설정
ma_window = st.sidebar.slider("추세선 평활화 기간 (일)", min_value=5, max_value=60, value=10, step=1)

if st.sidebar.button("데이터 조회"):
    with st.spinner("데이터를 분석 중입니다..."):
        # 날짜 포맷 변환 (pykrx용)
        str_start = start_date.strftime("%Y%m%d")
        str_end = end_date.strftime("%Y%m%d")
        
        # 데이터 가져오기
        raw_df = get_investor_data(selected_ticker, str_start, str_end)
        
        if raw_df.empty:
            st.error("해당 기간의 데이터가 없습니다.")
        else:
            df = raw_df[['외국인', '기관합계']].copy()
            
            # 1. 누적 순매수 계산
            df['외국인_누적'] = df['외국인'].cumsum()
            df['기관_누적'] = df['기관합계'].cumsum()
            
            # 2. 이동평균 적용 (추세 스무딩)
            df['외국인_이평'] = df['외국인_누적'].rolling(window=ma_window).mean()
            df['기관_이평'] = df['기관_누적'].rolling(window=ma_window).mean()
            
            # 3. 기울기 계산 (오늘 이동평균 값 - 어제 이동평균 값)
            df['외국인_기울기'] = df['외국인_이평'].diff()
            df['기관_기울기'] = df['기관_이평'].diff()
            
            # 4. 방향성 판단
            # 둘 다 양수이면 1 (동반 매수), 둘 다 음수이면 -1 (동반 매도), 엇갈리면 0
            conditions = [
                (df['외국인_기울기'] > 0) & (df['기관_기울기'] > 0),
                (df['외국인_기울기'] < 0) & (df['기관_기울기'] < 0)
            ]
            choices = [1, -1]
            df['동반방향'] = np.select(conditions, choices, default=0)
            
            df = df.dropna() # 이동평균으로 인한 결측치 제거
            
            # 5. Plotly를 이용한 시각화 (서브플롯)
            fig = make_subplots(
                rows=2, cols=1, 
                shared_xaxes=True,
                row_heights=[0.7, 0.3], # 위 차트 70%, 아래 차트 30% 비율
                vertical_spacing=0.05,
                subplot_titles=(f"{selected_name} ({selected_ticker}) 누적 순매수 추세 ({ma_window}일 이평)", 
                                "외국인/기관 동반 매수/매도 상태")
            )
            
            # 첫 번째 차트: 누적 추세선
            fig.add_trace(go.Scatter(x=df.index, y=df['외국인_이평'], mode='lines', name='외국인 추세', line=dict(color='blue', width=2)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=df['기관_이평'], mode='lines', name='기관 추세', line=dict(color='orange', width=2)), row=1, col=1)
            
            # 두 번째 차트: 방향성 막대 그래프
            # 색상 설정: 동반 매수(초록), 동반 매도(빨강), 엇갈림(회색)
            colors = ['rgba(0, 128, 0, 0.7)' if val == 1 else 'rgba(255, 0, 0, 0.7)' if val == -1 else 'rgba(200, 200, 200, 0.3)' for val in df['동반방향']]
            
            # 범례용 가짜 트레이스 (직관적인 범례를 위해)
            fig.add_trace(go.Bar(x=[df.index[0]], y=[0], marker_color='rgba(0, 128, 0, 0.7)', name='쌍끌이 매수 (+, +)'), row=2, col=1)
            fig.add_trace(go.Bar(x=[df.index[0]], y=[0], marker_color='rgba(255, 0, 0, 0.7)', name='쌍끌이 매도 (-, -)'), row=2, col=1)
            
            # 실제 데이터 막대 그래프
            fig.add_trace(go.Bar(x=df.index, y=df['동반방향'], marker_color=colors, showlegend=False), row=2, col=1)
            
            # 레이아웃 디테일 설정
            fig.update_layout(height=600, hovermode='x unified', margin=dict(l=20, r=20, t=60, b=20))
            fig.update_yaxes(title_text="누적 순매수 금액", row=1, col=1)
            fig.update_yaxes(title_text="일치 방향성", tickvals=[-1, 0, 1], ticktext=["동반매도", "엇갈림", "동반매수"], range=[-1.2, 1.2], row=2, col=1)
            
            # 화면에 출력
            st.plotly_chart(fig, use_container_width=True)
            
            # 요약 데이터 표 출력
            st.subheader("최근 동반 수급 상세 데이터")
            display_df = df[['외국인_기울기', '기관_기울기', '동반방향']].tail(10).iloc[::-1]
            display_df.columns = ['외국인 추세(기울기)', '기관 추세(기울기)', '쌍끌이 상태 (1:매수, -1:매도)']
            st.dataframe(display_df, use_container_width=True)
