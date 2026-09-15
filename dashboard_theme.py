"""On-page controls for Streamlit's native app theme."""

import streamlit as st

from theme_palette import THEME_OPTIONS

_NATIVE_THEME_NAMES = {"시스템": "System", "라이트": "Light", "다크": "Dark"}

# Streamlit has a native three-way theme switcher, but no public Python setter.
# This trusted component activates that switcher so its theme applies to charts,
# widgets, tables, navigation, and every page rather than just recoloring content.
NATIVE_THEME_BRIDGE = st.components.v2.component(
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
    """Show the same theme control above every page."""
    _, right = st.columns([5, 2], vertical_alignment="bottom")
    with right:
        choice = st.segmented_control(
            "화면 모드",
            THEME_OPTIONS,
            default="시스템",
            key="invest_theme_mode",
        )
    NATIVE_THEME_BRIDGE(data={"theme": _NATIVE_THEME_NAMES[choice or "시스템"]})
