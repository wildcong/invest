import streamlit as st
import pandas as pd
import FinanceDataReader as fdr
import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import zscore

# --- 페이지 설정 ---
st.set_page_config(page_title="Global Liquidity Tracker", layout="wide")
st.title("🌊 중기 주가 판단용: 글로벌 유동성 스코어 대시보드")
st.markdown("지급준비금, M2, TGA, 역레포 잔고의 **'기울기(4주 변화량)'**를 기반으로 시장의 유동성 흐름을 추적합니다.")

# --- 1. 데이터 가져오기 (FRED API 및 주가지수) ---
@st.cache_data(ttl=3600*24) # 24시간 캐싱
def load_data():
    start_date = datetime.datetime.now() - datetime.timedelta(days=365*5) # 최근 5년
    end_date = datetime.datetime.now()
    
    # 1-1. FRED 유동성 지표 티커
    fred_tickers = {
        'Reserves': 'WRESBAL',
        'TGA': 'WTREGEN',
        'Reverse_Repo': 'RRPONTSYD',
        'US_M2': 'M2SL',
        'KR_M2': 'MYAGM2KRM189N'
    }
    
    # 1-2. 주가지수 티커 (FinanceDataReader 기준)
    index_tickers = {
        'KOSPI': 'KS11',
        'NASDAQ': 'IXIC',
        'S&P500': 'S&P500'
    }
    
    series_list = []
    
    # FRED 데이터 수집
    for name, ticker in fred_tickers.items():
        try:
            series = fdr.DataReader(f'FRED:{ticker}', start_date, end_date)
            series = series[[ticker]].rename(columns={ticker: name})
            series_list.append(series)
        except Exception as e:
            st.error(f"FRED {name} 데이터 오류: {e}")

    # 주가지수 데이터 수집 (종가 Close 기준)
    for name, ticker in index_tickers.items():
        try:
            series = fdr.DataReader(ticker, start_date, end_date)
            # Close 컬럼만 추출하고 이름을 지수 이름으로 변경
            series = series[['Close']].rename(columns={'Close': name})
            series_list.append(series)
        except Exception as e:
            st.error(f"주가지수 {name} 데이터 오류: {e}")
            
    # 모든 데이터를 병합, 빈칸 채우기, 매주 금요일 기준으로 정리
    df = pd.concat(series_list, axis=1)
    df = df.ffill()
    df = df.resample('W-FRI').last().dropna()
    
    return df

df = load_data()

# --- 2. 기울기(Slope) 및 스코어 계산 ---
# 4주 전 대비 절대 변화량을 기울기로 사용 (주가지수는 스코어 계산에서 제외)
df_slope = df[['Reserves', 'US_M2', 'KR_M2', 'TGA', 'Reverse_Repo']].diff(periods=4).dropna()

# 방향성 부여 (TGA와 역레포는 감소해야 호재이므로 -1 곱함)
df_slope['Reserves_dir'] = df_slope['Reserves']
df_slope['US_M2_dir'] = df_slope['US_M2']
df_slope['KR_M2_dir'] = df_slope['KR_M2']
df_slope['TGA_dir'] = df_slope['TGA'] * -1
df_slope['Reverse_Repo_dir'] = df_slope['Reverse_Repo'] * -1

# 정규화 (Z-Score)
cols_to_score = ['Reserves_dir', 'US_M2_dir', 'KR_M2_dir', 'TGA_dir', 'Reverse_Repo_dir']
df_zscore = df_slope[cols_to_score].apply(zscore)

# 종합 유동성 스코어 산출
df_zscore['Liquidity_Score'] = df_zscore.sum(axis=1)

# 주가지수 데이터를 Z-score 데이터프레임과 날짜를 맞추어 합침 (주가 비교용)
df_final = df_zscore.copy()
df_final['KOSPI'] = df.loc[df_final.index, 'KOSPI']
df_final['NASDAQ'] = df.loc[df_final.index, 'NASDAQ']
df_final['S&P500'] = df.loc[df_final.index, 'S&P500']


# --- 3. 대시보드 UI 및 차트 시각화 ---
st.subheader("📊 유동성 스코어 vs 주가지수 상관관계")

# 비교할 주가지수 선택 라디오 버튼
selected_index = st.radio("비교할 주가지수를 선택하세요:", ('S&P500', 'NASDAQ', 'KOSPI'), horizontal=True)

# 이중 Y축 차트 생성
fig = make_subplots(specs=[[{"secondary_y": True}]])

# 1. 유동성 스코어 (왼쪽 Y축, 파란색 영역형 차트)
fig.add_trace(go.Scatter(
    x=df_final.index, y=df_final['Liquidity_Score'],
    mode='lines', name='Liquidity Score',
    fill='tozeroy', line=dict(color='rgba(65, 105, 225, 0.6)', width=2)
), secondary_y=False)

# 2. 선택한 주가지수 (오른쪽 Y축, 빨간색 선 차트)
fig.add_trace(go.Scatter(
    x=df_final.index, y=df_final[selected_index],
    mode='lines', name=f'{selected_index} Index',
    line=dict(color='firebrick', width=2.5)
), secondary_y=True)

# 0선 (기준선) 추가
fig.add_hline(y=0, line_dash="dash", line_color="gray", annotation_text="Liquidity Neutral (0)", secondary_y=False)

# 차트 레이아웃 디자인
fig.update_layout(
    height=600,
    template="plotly_white",
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)
# Y축 이름 설정
fig.update_yaxes(title_text="<b>Liquidity Z-Score</b>", secondary_y=False)
fig.update_yaxes(title_text=f"<b>{selected_index} Points</b>", secondary_y=True)

st.plotly_chart(fig, use_container_width=True)

# 개별 지표의 기울기 기여도 확인
st.subheader("🔍 개별 지표별 유동성 기여도 흐름 (최근 1년)")
st.line_chart(df_zscore[cols_to_score].tail(52)) # 최근 52주 데이터

# 원본 데이터 표
with st.expander("데이터 테이블 보기"):
    st.dataframe(df_final.tail(20).sort_index(ascending=False))
