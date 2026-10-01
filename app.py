import streamlit as st
import importlib

import dashboard_theme


# Community Cloud can update page files without restarting the Python process.
# If an older dashboard_theme is still in sys.modules, reload it before a page
# imports symbols that were introduced in the new deployment.
if not callable(getattr(dashboard_theme, "style_chart", None)):
    dashboard_theme = importlib.reload(dashboard_theme)


st.set_page_config(
    page_title="투자 시장 대시보드",
    page_icon="📊",
    layout="wide",
)

dashboard_theme.render_theme_picker()

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
