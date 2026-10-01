"""
ui_styles.py — Dash/Gradio 内联样式字典。
集中管理，避免散落在回调里。
"""
from typing import Any, Dict

# ── 基础按钮 ──
BASE_BTN_STYLE: Dict[str, Any] = {
    "border": "none",
    "padding": "8px 16px",
    "borderRadius": "6px",
    "cursor": "pointer",
    "marginRight": "6px",
    "fontWeight": "bold",
    "fontSize": "13px",
}

BUTTON_STYLE_PRIMARY = {**BASE_BTN_STYLE, "backgroundColor": "#1a73e8", "color": "white"}
BUTTON_STYLE_DANGER  = {**BASE_BTN_STYLE, "backgroundColor": "#ef4444", "color": "white"}
BUTTON_STYLE_SECONDARY = {**BASE_BTN_STYLE, "backgroundColor": "#e2e8f0", "color": "#475569"}

# ── 章节标题 ──
SECTION_HEADER_STYLE = {
    "fontSize": "14px",
    "fontWeight": "bold",
    "color": "#1e293b",
    "marginBottom": "8px",
}

# ── 缩放工具栏 ──
ZOOM_TOOLBAR_STYLE = {
    "backgroundColor": "white",
    "border": "1px solid #d1d5db",
    "borderRadius": "6px",
    "padding": "6px 12px",
    "cursor": "pointer",
    "fontSize": "13px",
    "color": "#475569",
}

# ── 进度条 ──
BAR_DONE = {
    "width": "100%", "height": "100%",
    "background": "linear-gradient(90deg, #10b981, #34d399)",
    "borderRadius": "6px",
}
BAR_ERROR = {"width": "100%", "height": "100%", "background": "#ef4444", "borderRadius": "6px"}
BAR_LOADING = {"className": "indeterminate-bar", "width": "100%", "height": "100%"}

EMPTY_BAR_STYLE = {
    "width": "0%", "height": "100%",
    "background": "linear-gradient(90deg, #10b981, #34d399)",
    "borderRadius": "6px",
}
EMPTY_BAR_MINI_STYLE = {"width": "0%", "height": "100%", "background": "transparent"}

# ── 空对象 ──
EMPTY_DAG = {"nodes": [], "edges": []}
EMPTY_STATES: Dict[str, Any] = {}

# ── Markdown ──
MARKDOWN_PRE_STYLE = {"whiteSpace": "pre-wrap", "fontSize": "14px", "lineHeight": "1.8"}

# ── 提示文字 ──
HINT_BASE_STYLE = {"fontSize": "13px", "marginTop": "8px", "fontStyle": "italic"}
HINT_STYLES = {
    "loading": {**HINT_BASE_STYLE, "color": "#f59e0b", "fontWeight": "bold"},
    "ready":   {**HINT_BASE_STYLE, "color": "#10b981", "fontWeight": "bold"},
    "running": {**HINT_BASE_STYLE, "color": "#10b981", "fontWeight": "bold"},
    "done":    {**HINT_BASE_STYLE, "color": "#10b981"},
    "skipped": {**HINT_BASE_STYLE, "color": "#9ca3af"},
    "failed":  {**HINT_BASE_STYLE, "color": "#ef4444"},
    "pending": {**HINT_BASE_STYLE, "color": "#f59e0b"},
}
