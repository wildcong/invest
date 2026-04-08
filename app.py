import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
import FinanceDataReader as fdr

# 페이지 설정
st.set_page_config(page_title="KOSPI 200 무제한 누적 수급", layout="wide")

# ==========================================
# 1. KOSPI 200 전 종목 리스트 가져오기
# ==========================================
@st.cache_data(ttl=86400)
def get_kospi200_list():
    try:
        df_kospi = fdr.StockListing('KOSPI')
        df_200 = df_kospi.sort_values('MarCap', ascending=False).head(200)
        return dict(zip(df_200['Name'], df_200['Code']))
    except Exception as e:
        st.error(f"종목을 불러오는 중 오류가 발생했습니다. 앱을 재부팅해 주세요. ({e})")
        return {"삼성전자": "005930", "SK하이닉스": "000660"}

# ==========================================
# 2. 네이버 증권 스크래핑 (30일 제한 돌파!)
# ==========================================
@st.cache_data(show_spinner=False, ttl=3600)
def get_naver_investor_data(ticker, days):
    import math
    # 한 페이지에 20일치 데이터가 들어있음
    pages = math.ceil(days / 20)
    df_list = []
    
    # 봇(Bot) 차단 방지용 헤더
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/frgn.naver?code={ticker}&page={page}"
        res = requests.get(url, headers=headers)
        tables = pd.read_html(res.text, encoding='euc-kr')
        
        # 3번째 표가 투자자별 매매동향 표
        df = tables[2].copy()
        
        # 다중 인덱스 제거 및 컬럼명 강제 지정 (네이버 증권 양식)
        df.columns = ['Date', 'Close', 'Diff', 'Ratio', 'Vol', '기관', '외국인', 'Hold', 'HoldRatio']
        df = df.dropna(subset=['Date']) # 빈 줄 제거
        df_list.append(df)
        
    final_df = pd.concat(df_list, ignore_index=True)
    final_df = final_df[['Date', '기관', '외국인']].copy()
    
    # 날짜 포맷 맞추기 및 쉼표(,) 제거 후 숫자로 변환
    final_df['Date'] = pd.to_datetime(final_df['Date'].str.replace('.', '-'))
    for col in ['기관', '외국인']:
        final_df[col] = final_df[col].astype(str).str.replace(',', '').str.replace('+', '').astype(float)
        
    # 과거 데이터가 위로 오도록 정렬
    final_df = final_df.sort_values('Date').set_index('Date')
    
    # 사용자가 요청한 일수만큼만 정확히 잘라서 반환
    return final_df.tail(days)

# ==========================================
# 3. 화면 UI 및 자동 실행 로직 (버튼 제거)
# ==========================================
st.title("📈 KOSPI 200 장기 누적 수급 분석기")
st.markdown("조회 버튼을 누를 필요 없이 **종목이나 기간을 변경하면 즉시 차트가 반영**됩니다.")

# 종목 리스트 로딩
with st.spinner("종목 리스트 로딩 중..."):
    kospi_dict = get_kospi200_list()

# 사이드바 설정
st.sidebar.header("설정 (Settings)")
selected_name = st.sidebar.selectbox("종목 선택 (시총 상위 200)", list(kospi_dict.keys()))
selected_ticker = kospi_dict[selected_name]

# 원하는 일수 슬라이더 (최소 한 달 ~ 최대 1년)
analyze_days = st.sidebar.slider("조회 기간 (영업일 기준)", min_value=30, max_value=240, value=120, step=10, help="240일은 약 1년치 데이터입니다.")

# ==== [버튼 없이 바로 실행되는 영역] ====
with st.spinner(f"{selected_name} 수급 데이터 {analyze_days}일치 수집 중..."):
    df = get_naver_investor_data(selected_ticker, days=analyze_days)
    
    if not df.empty:
        # 데이터 누적합 계산
        df['외국인_누적'] = df['외국인'].cumsum()
        df['기관_누적'] = df['기관'].cumsum()
        
        # 차트 그리기
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df['외국인_누적'], mode='lines', name='외국인 누적 매수', line=dict(color='blue', width=3)))
        fig.add_trace(go.Scatter(x=df.index, y=df['기관_누적'], mode='lines', name='기관 누적 매수', line=dict(color='orange', width=3)))
        
        # 0점 기준선 (회색 점선)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        
        fig.update_layout(
            title=f"{selected_name} 최근 {analyze_days}일 외국인/기관 누적 수급 추세", 
            hovermode="x unified",
            height=600,
            xaxis_title="날짜",
            yaxis_title="누적 순매수 수량 (주)"
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # 하단 요약표
        st.subheader(f"📊 최근 5일 데이터 요약")
        st.table(df[['외국인_누적', '기관_누적']].tail(5).iloc[::-1])
