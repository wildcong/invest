import streamlit as st


st.set_page_config(
    page_title="투자 시장 대시보드",
    page_icon="📊",
    layout="wide",
)

navigation = st.navigation(
    [
        st.Page(
            "pages/flow_scanner.py",
            title="국내 수급 스캐너",
            icon="📊",
            default=True,
        ),
        st.Page(
            "pages/program_trade.py",
            title="비차익 프로그램매매",
            icon="🇰🇷",
        ),
        st.Page(
            "pages/us_liquidity.py",
            title="미국 유동성",
            icon="🇺🇸",
        ),
    ],
    position="top",
)
navigation.run()
