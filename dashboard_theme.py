"""A quiet shared layout that follows Streamlit's native app theme."""

import streamlit as st

from theme_palette import THEME_OPTIONS

_NATIVE_THEME_NAMES = {"시스템": "System", "라이트": "Light", "다크": "Dark"}

# Keep native colors, controls, focus rings and warnings in both color modes.
DASHBOARD_CSS = """
<style>
  [data-testid="stMainBlockContainer"] {
    max-width: 1320px;
    padding-top: 1.5rem;
    padding-bottom: 2.5rem;
  }
  [data-testid="stVerticalBlock"] { gap: 0.85rem; }
  h1 { font-size: 1.9rem !important; letter-spacing: -0.045em; }
  h2 { font-size: 1.4rem !important; letter-spacing: -0.03em; }
  h3 { font-size: 1.1rem !important; letter-spacing: -0.02em; }
  [data-testid="stMetricValue"] { font-size: 1.55rem; }
  [data-testid="stMetricLabel"] { font-size: 0.82rem; }
  [data-testid="stCaptionContainer"] { line-height: 1.5; }
  [data-testid="stExpander"] { border-radius: 0.75rem; }
  [data-testid="stPlotlyChart"] { border-radius: 0.75rem; }
  @media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] { padding: 1rem 1rem 2rem; }
    h1 { font-size: 1.55rem !important; }
    [data-testid="stMetricValue"] { font-size: 1.3rem; }
  }
</style>
"""


def style_chart(figure, *, height: int = 440):
    """Unify chart spacing without changing traces, units or theme colors."""
    figure.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 32, "b": 8},
        font={"size": 12},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0},
        hoverlabel={"font_size": 12},
    )
    figure.update_xaxes(showgrid=False, zeroline=False)
    figure.update_yaxes(gridcolor="rgba(128, 128, 128, 0.12)", zeroline=False)
    return figure

# Streamlit has a native three-way theme switcher, but no public Python setter.
# This trusted component activates that switcher so its theme applies to charts,
# widgets, tables, navigation, and every page rather than just recoloring content.
def _native_theme_bridge():
    # Register against the active Streamlit runtime. A Cloud code reload or
    # AppTest can create a new runtime while the Python module stays imported.
    return st.components.v2.component(
        "invest_native_theme_bridge",
        css=":host { display: none; }",
        js="""
    export default function({ data }) {
      const wanted = data.theme;
      if (window.__investAppliedTheme === wanted) return;

      const menuButton = document.querySelector('[data-testid="stMainMenuButton"]');
      if (!menuButton) return;

      const itemSelector = `[data-testid="stMainMenuItem-theme-${wanted}"]`;
      const wasOpen = menuButton.getAttribute('aria-expanded') === 'true';
      if (!wasOpen) menuButton.click();

      const applySelection = () => {
        const item = document.querySelector(itemSelector);
        if (!item) return false;
        if (item.getAttribute('aria-checked') !== 'true') item.click();
        if (!wasOpen) menuButton.click();
        window.__investAppliedTheme = wanted;
        return true;
      };

      const observer = new MutationObserver(() => {
        if (applySelection()) observer.disconnect();
      });
      observer.observe(document.body, { childList: true, subtree: true });
      // The menu may already be in the DOM when the component mounts.
      if (applySelection()) observer.disconnect();
      const timeout = setTimeout(() => observer.disconnect(), 1500);
      return () => { observer.disconnect(); clearTimeout(timeout); };
    }
        """,
    )


def render_theme_picker() -> None:
    """Keep display preferences available in one small menu."""
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.caption("INVEST  /  시장 대시보드")
        with st.popover("화면 설정"):
            choice = st.segmented_control(
                "화면 모드",
                THEME_OPTIONS,
                default="시스템",
                key="invest_theme_mode",
                wrap=True,
            )
    _native_theme_bridge()(data={"theme": _NATIVE_THEME_NAMES[choice or "시스템"]})
