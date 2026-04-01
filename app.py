import streamlit as st
import pandas as pd
import FinanceDataReader as fdr
import datetime
import plotly.graph_objects as go
from scipy.stats import zscore

# --- 페이지 설정 ---
st.set_page_config(page_title="Global Liquidity Tracker", layout="wide")
st.title("🌊 중기 주가 판단용: 글로벌 유동성 스코어 대시보드")
st.markdown("지급준비금, M2, TGA, 역레포 잔고의 **'기울기(4주 변화량)'**를 기반으로 시장의 유동성 흐름을 추적합니다.")

# --- 1. 데이터 가져오기 (FRED API) ---
@st.cache_data(ttl=3600*24) # 24시간 캐싱
def load_data():
    start_date = datetime.datetime.now() - datetime.timedelta(days=365*5) # 최근 5년
    end_date = datetime.datetime.now()
    
    # FRED 티커: WRESBAL(지급준비금), WTREGEN(TGA), RRPONTSYD(역레포), M2SL(미국M2), MYAGM2KRM189N(한국M2)
    tickers = {
        'Reserves': 'WRESBAL',
        'TGA': 'WTREGEN',
        'Reverse_Repo': 'RRPONTSYD',
        'US_M2': 'M2SL',
        'KR_M2': 'MYAGM2KRM189N'
    }
    
    df = pd.DataFrame()
    for name, ticker in tickers.items():
        try:
            series = web.DataReader(ticker, 'fred', start_date, end_date)
            df[name] = series[ticker]
        except Exception as e:
            st.error(f"{name} 데이터를 불러오는 중 오류 발생: {e}")
            
    # 일간/주간/월간 데이터를 '주간(금요일 기준)'으로 통일하고 빈칸은 이전 값으로 채움(ffill)
    df = df.resample('W-FRI').ffill().dropna()
    return df

df = load_data()

# --- 2. 기울기(Slope) 및 스코어 계산 ---
# 4주(약 1개월) 전 대비 절대 변화량을 기울기로 사용
df_slope = df.diff(periods=4).dropna()

# 방향성 부여 (역레포와 TGA는 줄어들어야(음수) 시장에 호재이므로 -1을 곱함)
df_slope['Reserves_dir'] = df_slope['Reserves']
df_slope['US_M2_dir'] = df_slope['US_M2']
df_slope['KR_M2_dir'] = df_slope['KR_M2']
df_slope['TGA_dir'] = df_slope['TGA'] * -1
df_slope['Reverse_Repo_dir'] = df_slope['Reverse_Repo'] * -1

# 정규화 (Z-Score): 각 지표의 단위가 다르므로 과거 평균 대비 편차로 변환
cols_to_score = ['Reserves_dir', 'US_M2_dir', 'KR_M2_dir', 'TGA_dir', 'Reverse_Repo_dir']
df_zscore = df_slope[cols_to_score].apply(zscore)

# 종합 유동성 스코어 산출 (일단 동일 가중치 부여, 필요시 곱하는 수치 조정 가능)
df_zscore['Liquidity_Score'] = df_zscore.sum(axis=1)

# --- 3. 대시보드 UI 및 차트 시각화 ---
st.subheader("📊 종합 유동성 스코어 (Liquidity Score) 추이")

# Plotly를 이용한 동적 차트
fig = go.Figure()

# 유동성 스코어 라인 (0보다 크면 유동성 확장 국면, 작으면 축소 국면)
fig.add_trace(go.Scatter(
    x=df_zscore.index, y=df_zscore['Liquidity_Score'],
    mode='lines', name='Liquidity Score',
    line=dict(color='royalblue', width=2)
))

# 0선 (기준선) 추가
fig.add_hline(y=0, line_dash="dash", line_color="gray", annotation_text="Neutral (0)")

fig.update_layout(
    xaxis_title="Date",
    yaxis_title="Z-Score (Sum of Slopes)",
    height=500,
    template="plotly_white"
)
st.plotly_chart(fig, use_container_width=True)

# 개별 지표의 기울기 기여도 확인
st.subheader("🔍 개별 지표별 유동성 기여도 (최근 1년)")
st.line_chart(df_zscore[cols_to_score].tail(52)) # 최근 52주 데이터

# 원본 데이터 표
with st.expander("데이터 테이블 보기"):
    st.dataframe(df.tail(20).sort_index(ascending=False))
