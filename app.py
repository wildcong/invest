import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import FinanceDataReader as fdr
import time

# ==========================================
# 🔒 보안 설정 (Streamlit Secrets)
# ==========================================
APP_KEY = st.secrets["KIS_APP_KEY"]
APP_SECRET = st.secrets["KIS_APP_SECRET"]
URL_BASE = "https://openapi.koreainvestment.com:9443"

st.set_page_config(page_title="수급 쌍끌이 스캐너", layout="wide")

# ==========================================
# 1. 데이터 수집 함수들
# ==========================================
@st.cache_data(ttl=86400)
def get_kospi200_list():
    try:
        df_kospi = fdr.StockListing('KOSPI')
        mcap_col = 'Marcap' if 'Marcap' in df_kospi.columns else 'MarCap'
        df_200 = df_kospi.sort_values(mcap_col, ascending=False).head(200)
        return dict(zip(df_200['Name'], df_200['Code']))
    except Exception:
        return {"삼성전자": "005930", "SK하이닉스": "000660"}

@st.cache_data(ttl=86000)
def get_access_token():
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body))
    if res.status_code == 200:
        return res.json().get("access_token")
    else:
        st.error(f"토큰 발급 실패: {res.text}")
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_investor_data(ticker, access_token):
    headers = {
        "content-type": "application/json; charset=utf-8", 
        "authorization": f"Bearer {access_token}",
        "appkey": APP_KEY, "appsecret": APP_SECRET, 
        "tr_id": "FHKSW03010000", "custtype": "P"
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    
    try:
        res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor", headers=headers, params=params)
        res_json = res.json()
        
        if res.status_code == 200 and 'output' in res_json:
            df = pd.DataFrame(res_json['output'])
            if df.empty: return pd.DataFrame()
            
            df = df[['stck_bsop_date', 'stck_clpr', 'frgn_ntby_tr_pbmn', 'orgn_ntby_tr_pbmn']].copy()
            df.columns = ['Date', 'Price', 'Foreign_Amt', 'Inst_Amt']
            df['Date'] = pd.to_datetime(df['Date'])
            for col in ['Price', 'Foreign_Amt', 'Inst_Amt']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.dropna()
            
            df['F_억'] = df['Foreign_Amt'] / 100000000
            df['I_억'] = df['Inst_Amt'] / 100000000
            return df.sort_values('Date').set_index('Date')
    except Exception:
        pass
    return pd.DataFrame()

def scan_all_stocks(stock_dict, token):
    valid_stocks = {}
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(stock_dict)
    
    for i, (name, ticker) in enumerate(stock_dict.items()):
        status_text.text(f"🚀 스캔 중 ({i+1}/{total}): {name}")
        df = get_investor_data(ticker, token)
        if not df.empty and len(df) >= 5:
            f_sum = df['F_억'].tail(5).sum()
            i_sum = df['I_억'].tail(5).sum()
            if f_sum > 0 and i_sum > 0:
                valid_stocks[name] = f"{name} (↑↑)"
            elif f_sum < 0 and i_sum < 0:
                valid_stocks[name] = f"{name} (↓↓)"
        progress_bar.progress((i + 1) / total)
        time.sleep(0.05)
        
    status_text.empty()
    progress_bar.empty()
    return valid_stocks

# ==========================================
# 3. 메인 화면 구성 및 필터링
# ==========================================
st.title("📊 KOSPI 200 쌍끌이 스캐너")

kospi_dict = get_kospi200_list()
token = get_access_token()

if 'current_idx' not in st.session_state:
    st.session_state.current_idx = 0

h_col1, h_col2, h_col3 = st.columns([1, 1.5, 1.2])

with h_col1:
    is_filtered = st.checkbox("🔥 5일 동방향 필터")

with h_col2:
    period = st.select_slider("분석 기간", options=[5, 10, 15, 20, 25, 30], value=30, label_visibility="collapsed")

if is_filtered:
    if 'filtered_map' not in st.session_state:
        if token:
            st.session_state.filtered_map = scan_all_stocks(kospi_dict, token)
        else:
            st.error("API 토큰 발급 실패")
            st.stop()
    display_names = list(st.session_state.filtered_map.values())
    name_lookup = {v: k for k, v in st.session_state.filtered_map.items()}
else:
    display_names = list(kospi_dict.keys())
    name_lookup = {n: n for n in display_names}

if not display_names:
    st.warning("조건에 맞는 종목이 없습니다.")
    display_names = ["삼성전자"]; name_lookup = {"삼성전자": "삼성전자"}

def go_prev():
    if st.session_state.current_idx > 0:
        st.session_state.current_idx -= 1
        st.session_state.stock_selector = display_names[st.session_state.current_idx]

def go_next():
    if st.session_state.current_idx < len(display_names) - 1:
        st.session_state.current_idx += 1
        st.session_state.stock_selector = display_names[st.session_state.current_idx]

def on_change():
    if 'stock_selector' in st.session_state and st.session_state.stock_selector in display_names:
        st.session_state.current_idx = display_names.index(st.session_state.stock_selector)

c1, c2, c3 = st.columns([1, 2, 1])
with c1: st.button("⬅️ 이전", on_click=go_prev, use_container_width=True)
with c2:
    if st.session_state.current_idx >= len(display_names): st.session_state.current_idx = 0
    selected_disp = st.selectbox("종목", display_names, index=st.session_state.current_idx, 
                                 key="stock_selector", on_change=on_change, label_visibility="collapsed")
with c3: st.button("다음 ➡️", on_click=go_next, use_container_width=True)

selected_real = name_lookup.get(selected_disp, selected_disp)
selected_ticker = kospi_dict.get(selected_real, "005930")

# ==========================================
# 4. 차트 및 표 시각화
# ==========================================
if token:
    df = get_investor_data(selected_ticker, token)
    
    if not df.empty:
        df_disp = df.tail(period).copy()
        curr_p = df_disp['Price'].iloc[-1]
        prev_p = df_disp['Price'].iloc[-2] if len(df_disp) > 1 else curr_p
        diff = curr_p - prev_p
        ratio = (diff / prev_p) * 100
        
        f_sum, i_sum = df_disp['F_억'].tail(5).sum(), df_disp['I_억'].tail(5).sum()
        if f_sum > 0 and i_sum > 0: 
            b_html = '<span style="background-color:#ff4b4b;color:white;padding:2px 6px;border-radius:4px;font-size:0.8rem;">쌍끌이 매수 ↑↑</span>'
        elif f_sum < 0 and i_sum < 0: 
            b_html = '<span style="background-color:#31333f;color:white;padding:2px 6px;border-radius:4px;font-size:0.8rem;">쌍끌이 매도 ↓↓</span>'
        else: 
            b_html = '<span style="background-color:#f0f2f6;color:#31333f;padding:2px 6px;border-radius:4px;font-size:0.8rem;">엇갈림</span>'

        with h_col3:
            p_c = "red" if diff > 0 else "blue" if diff < 0 else "gray"
            st.markdown(f'<div style="text-align:right;line-height:1.4;"><div>{b_html}</div><div style="font-size:1.05rem;font-weight:bold;">{curr_p:,.0f} <span style="color:{p_c};font-size:0.9rem;">({"▲" if diff>0 else "▼" if diff<0 else ""}{abs(diff):,.0f}, {ratio:.2f}%)</span></div></div>', unsafe_allow_html=True)

        df_disp['F_누적'] = df_disp['F_억'].cumsum()
        df_disp['I_누적'] = df_disp['I_억'].cumsum()

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=df_disp.index, y=df_disp['F_누적'], name='외인누적(억)', line=dict(color='blue', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df_disp.index, y=df_disp['I_누적'], name='기관누적(억)', line=dict(color='orange', width=3)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df_disp.index, y=df_disp['Price'], name='주가', line=dict(color='red', width=1.5, dash='dot')), secondary_y=True)
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.update_layout(title=f"<b>{selected_real}</b>", hovermode="x unified", height=450, margin=dict(l=5,r=5,t=50,b=5), legend=dict(orientation="h", y=1.1, x=0.5, xanchor='center'))
        st.plotly_chart(fig, use_container_width=True)

        st.write("##### 📋 상세 내역 (단위: 억원)")
        res_df = df_disp[['Price','F_억','I_억','F_누적','I_누적']].iloc[::-1].copy()
        res_df.columns = ['주가','외인_일일','기관_일일','외인_누적','기관_누적']
        st.dataframe(res_df.style.format("{:,.1f}"), use_container_width=True)
    else:
        st.error("데이터를 불러올 수 없습니다. 아래 API 로그를 확인해 주세요.")

# ==========================================
# 🚨 [새로 추가된 핵심 기능] API 디버그 로그
# ==========================================
st.markdown("---")
with st.expander("🛠️ 시스템 로그 보기 (에러 원인 파악용)"):
    if token:
        st.write(f"현재 선택된 종목: **{selected_real}** (코드: {selected_ticker})")
        
        # 캐시 없이 실시간으로 API에 한 번 찔러보고 응답을 화면에 뿌려주는 로직
        headers = {
            "content-type": "application/json; charset=utf-8", 
            "authorization": f"Bearer {token}",
            "appkey": APP_KEY, "appsecret": APP_SECRET, 
            "tr_id": "FHKSW03010000", "custtype": "P"
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": selected_ticker}
        
        try:
            raw_res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor", headers=headers, params=params)
            
            st.write(f"**HTTP 상태 코드:** {raw_res.status_code}")
            
            try:
                st.json(raw_res.json())
            except:
                st.text("JSON 변환 실패. 원본 텍스트:")
                st.text(raw_res.text)
                
        except Exception as e:
            st.error(f"서버 연결 자체가 실패했습니다. 에러: {str(e)}")
    else:
        st.warning("토큰이 없어 API 테스트를 실행할 수 없습니다.")
