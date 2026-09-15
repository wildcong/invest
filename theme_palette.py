"""Colors and labels shared by the dashboard theme control and charts."""

THEME_OPTIONS = ("시스템", "라이트", "다크")
DARK_CUMULATIVE_LINE_COLOR = "#facc15"
LIGHT_CUMULATIVE_LINE_COLOR = "#a16207"
FALLBACK_CUMULATIVE_LINE_COLOR = "#b58900"


def cumulative_line_color(mode: str | None, system_theme: str | None) -> str:
    """Choose a visible yellow for either native Streamlit theme."""
    if mode == "다크" or (mode in (None, "시스템") and system_theme == "dark"):
        return DARK_CUMULATIVE_LINE_COLOR
    if mode == "라이트" or (mode in (None, "시스템") and system_theme == "light"):
        return LIGHT_CUMULATIVE_LINE_COLOR
    return FALLBACK_CUMULATIVE_LINE_COLOR
