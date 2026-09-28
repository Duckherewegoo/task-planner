"""
config.py — 全局配置中心（生产最终版改版）
══════════════════════════════════════════════════
所有模块的唯一依赖源。集中管理：
  ✅ 日志 / DashScope / 模型 / 思考模式
  ✅ 超时重试 / MongoDB / Gradio
  ✅ 渲染导出（SVG / PNG / PDF / DOT / HTML / JSON）
  ✅ 任务状态枚举（全模块共用）
  ✅ Prompt 模板（JSON 大括号全部 {{}} 转义）

Changelog:
  - 修复致命 NameError Bug
  - 引入 _env() 防御性解析器
  - 安全与正则预编译优化
  - 清理冗余导入与统一风格
  - 100% 向后兼容
  - ✅ P0-1 修复：DEBUG 默认值改为 False（生产优先）
  - ✅ P0-2 修复：NODE_STATUS_CODE_MAP 4→timeout、补 5→skipped
  - ✅ P0-3 修复：_env() 里跨行 f-string 改为 logger 占位符（兼容 Python < 3.12）
  - ✅ P1-2 修复：移除 WERKZEUG_RUN_MAIN 硬编码
  - ✅ P1-3 修复：敏感词正则加词边界，减少误伤
  - ✅ P1-1 修复：INTENT_PROMPT 的 category 白名单补 consultation
  - ✅ 修复：GRADIO_PORT 与 DASH_PORT 默认值冲突
  - ✅ 统一：logger 全部改为 %s 延迟格式化风格
"""

import os
import re
import logging
import warnings
from typing import Any, Callable, Dict, List, Set, FrozenSet
from string import Template

# =============================================================================
# 0. 项目根目录与基础环境加载
# =============================================================================
from pathlib import Path

# config.py = <root>/src/task_planner/infrastructure/config.py
#   parents[0] = infrastructure/
#   parents[1] = task_planner/
#   parents[2] = src/
#   parents[3] = <root>/

try:
    from dotenv import load_dotenv
    from pathlib import Path
    _PROJECT_ROOT = Path(__file__).resolve().parents[3]
    # 显式指定，无论从哪个目录跑都能找到同一个 .env
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    passlogger = logging.getLogger(__name__)

# =============================================================================
# 1. 环境变量解析工具 (Robust Env Parser)
# =============================================================================


def _env(key: str, default: Any = None, cast: Callable = str) -> Any:
    """
    安全获取环境变量并进行类型转换。
    如果转换失败，记录警告并返回默认值，避免启动时直接崩溃。
    """
    val = os.getenv(key)
    if val is None:
        return default

    try:
        if cast is bool:
            return val.strip().lower() in ("true", "1", "yes", "on", "y")
        return cast(val)
    except (ValueError, TypeError):
        # ✅ P0-3 修复：改为 %s 占位符风格，兼容 Python < 3.12
        logger.warning(
            "环境变量 %s='%s' 无法转换为 %s，已回退至默认值: %s",
            key, val, cast.__name__, default,
        )
        return default


# =============================================================================
# 2. 日志
# =============================================================================
# ✅ P0-1 修复：DEBUG 默认 False（生产安全优先）
DEBUG = _env("DEBUG", False, bool)
LOG_DIR = _env("LOG_DIR", os.path.join(_PROJECT_ROOT, "logs"))
LOG_FILE = _env("LOG_FILE", "task_planner.log")
LOG_MAX_DAYS = _env("LOG_MAX_DAYS", 7, int)
LOG_CONSOLE_LEVEL = _env("LOG_CONSOLE_LEVEL", "DEBUG" if DEBUG else "INFO")
DASH_DISABLE_VERSION_CHECK = _env("DASH_DISABLE_VERSION_CHECK", "True", str)
# =============================================================================
# 3. DashScope
# =============================================================================
DASHSCOPE_API_KEY = _env("DASHSCOPE_API_KEY", "")
# 特殊处理：空字符串转为 None
_dashscope_url = _env("DASHSCOPE_BASE_URL", "")
DASHSCOPE_BASE_URL = _dashscope_url if _dashscope_url else None
DASHSCOPE_REGION = _env("DASHSCOPE_REGION", "cn-beijing")
USE_MOCK_LLM = _env("USE_MOCK_LLM", False, bool)

# =============================================================================
# 4. 模型（真实模型，零 Mock）
# =============================================================================
LLM_INTENT_MODEL = _env("LLM_INTENT_MODEL", "qwen3.7-flash-2026-07-15")
LLM_PLANNER_MODEL = _env("LLM_PLANNER_MODEL", "qwen3.7-flash-2026-07-15")
LLM_NODE_MODEL = _env("LLM_NODE_MODEL", "qwen3.7-flash-2026-07-15")

# =============================================================================
# 5. 思考模式
# =============================================================================
LLM_INTENT_ENABLE_THINKING = _env("LLM_INTENT_ENABLE_THINKING", False, bool)
LLM_PLANNER_ENABLE_THINKING = _env("LLM_PLANNER_ENABLE_THINKING", True, bool)
LLM_NODE_ENABLE_THINKING = _env("LLM_NODE_ENABLE_THINKING", False, bool)
LLM_THINKING_BUDGET = _env("LLM_THINKING_BUDGET", 81920, int)

# =============================================================================
# 6. 超时 / 重试
# =============================================================================
LLM_TIMEOUT = _env("LLM_TIMEOUT", 120, int)  # 秒
LLM_MAX_RETRIES = _env("LLM_MAX_RETRIES", 3, int)
LLM_RETRY_BACKOFF = _env("LLM_RETRY_BACKOFF", 2, int)
LLM_TASK_TOTAL_TIMEOUT = _env("LLM_TASK_TOTAL_TIMEOUT", 300, int)
MAX_TASK_RETRIES = _env("MAX_TASK_RETRIES", 3, int)
# 评估套件：单 case 延迟接受阈值（秒）
LATENCY_ACCEPTANCE_THRESHOLD = _env("LATENCY_ACCEPTANCE_THRESHOLD", 10.0, float)

# 单节点执行超时（秒）—— 防止一个节点卡死拖垮全局
# ⚠️ 若 LLM_NODE_ENABLE_THINKING=true，实际耗时可能接近 LLM_THINKING_BUDGET / tokens_per_sec
#    建议 LLM_NODE_TIMEOUT ≥ LLM_TIMEOUT，给思考模式留足余量
LLM_NODE_TIMEOUT = _env("LLM_NODE_TIMEOUT", 60, int)

# ✅ 自动校正：当节点启用思考模式时，确保 timeout 不低于全局 LLM_TIMEOUT
if LLM_NODE_ENABLE_THINKING and LLM_NODE_TIMEOUT < LLM_TIMEOUT:
    warnings.warn(
        "[Config] LLM_NODE_TIMEOUT({}s) < LLM_TIMEOUT({}s) 且节点思考模式已开启，"
        "自动将 LLM_NODE_TIMEOUT 提升至 {}s".format(
            LLM_NODE_TIMEOUT, LLM_TIMEOUT, LLM_TIMEOUT
        ),
        UserWarning,
    )
    LLM_NODE_TIMEOUT = LLM_TIMEOUT

# =============================================================================
# 7. MongoDB
# =============================================================================
MONGO_HOST = _env("MONGO_HOST", "task_mongodb")
MONGO_PORT = _env("MONGO_PORT", 27017, int)
MONGO_DB = _env("MONGO_DB", "task_planner_db")

# =============================================================================
# 8. Gradio
# =============================================================================
GRADIO_HOST = _env("GRADIO_HOST", "0.0.0.0")
GRADIO_PORT = _env("GRADIO_PORT", 7860, int)
ENABLE_MCP = _env("ENABLE_MCP", False, bool)

# =============================================================================
# 9. DASH
# =============================================================================
DASH_HOST = _env("DASH_HOST", "0.0.0.0")
# ✅ 修复：原默认 7860 与 GRADIO_PORT 冲突，改为 8050（Dash 官方默认端口）
DASH_PORT = _env("DASH_PORT", 8050, int)

# ⚠️ P1-2 修复：删除 WERKZEUG_RUN_MAIN = True
#    Werkzeug reloader 会用这个环境变量区分父/子进程。硬编码 True 会导致：
#      - 父进程误以为自己是子进程，不启动 HTTP 服务器
#      - 热重载（debug=True）失效
#    如果你需要传递"是否开发模式"，请使用上面的 DEBUG 变量。

# =============================================================================
# 10. 渲染 / 导出
# =============================================================================
RENDER_DPI = _env("RENDER_DPI", 300, int)
RENDER_FONT = _env("RENDER_FONT", "Noto Sans CJK SC")
RENDER_DIR = _env("RENDER_DIR", os.path.join(_PROJECT_ROOT, "exports"))
RENDER_WIDTH = _env("RENDER_WIDTH", 1200, int)
RENDER_HEIGHT = _env("RENDER_HEIGHT", 600, int)

# 边渲染参数
RENDER_EDGE_WIDTH = _env("RENDER_EDGE_WIDTH", 2.0, float)
RENDER_EDGE_LABEL_FONT_SIZE = _env("RENDER_EDGE_LABEL_FONT_SIZE", 10, int)
RENDER_SHOW_EDGE_LABELS = _env("RENDER_SHOW_EDGE_LABELS", True, bool)

# ── 流程图特性开关 ──
FLOWCHART_CYCLE_DETECTION = _env("FLOWCHART_CYCLE_DETECTION", True, bool)

FONT_FACE = (
    "'Noto Sans CJK SC', 'WenQuanYi Zen Hei', 'Microsoft YaHei', "
    "'SimHei', 'DejaVu Sans', Arial, sans-serif"
)

# ✅ 导出格式 —— 全部真实可用
#     svg  : pyvis HTML → cairosvg 提取 SVG
#     png  : SVG → cairosvg 转 PNG (300DPI)
#     pdf  : SVG → cairosvg 转 PDF (300DPI, A4)
#     dot  : networkx → pydot → DOT 源码
#     html : pyvis 完整交互式 HTML
#     json : 节点/边 JSON 数据
SUPPORTED_EXPORT_FORMATS = ["svg", "png", "pdf", "dot", "html", "json"]
DEFAULT_EXPORT_FORMAT = "svg"

# =============================================================================
# 11. Cytoscape 样式
# =============================================================================
CYTO_STYLESHEET: List[Dict[str, Any]] = [
    {
        "selector": "node",
        "style": {
            "label": "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "font-size": "13px",
            "font-family": "Arial, sans-serif",
            "width": "data(width)",
            "height": "label",
            "padding": "12px",
            "shape": "round-rectangle",
            "border-width": 2,
            "text-wrap": "wrap",
            "text-max-width": "180px",
            "transition-property": "background-color, border-color, opacity",
            "transition-duration": "0.3s",
        },
    },
    {"selector": ".state-ready", "style": {
        "background-color": "#fef3c7", "border-color": "#f59e0b", "border-width": 3,
    }},
    {"selector": ".state-running", "style": {
        "background-color": "#dbeafe", "border-color": "#3b82f6", "border-width": 3,
    }},
    {"selector": ".state-done", "style": {
        "background-color": "#d1fae5", "border-color": "#10b981", "opacity": 0.9,
    }},
    {"selector": ".state-failed", "style": {
        "background-color": "#fee2e2", "border-color": "#ef4444", "border-style": "dashed",
    }},
    {"selector": ".state-skipped", "style": {
        "background-color": "#f3f4f6", "border-color": "#9ca3af", "opacity": 0.5,
    }},
    # ✅ 补齐 state-timeout（NODE_STATUS_CODE_MAP[4] = "timeout" 对应的样式）
    #    配色取自 STATUS_COLOR[4] / STATUS_BORDER[4]，与后端状态语义一致
    {"selector": ".state-timeout", "style": {
        "background-color": "#f3e5f5", "border-color": "#9c27b0",
        "border-style": "dashed", "border-width": 3,
    }},
    {"selector": ".state-blocked", "style": {
        "background-color": "#f9fafb", "border-color": "#d1d5db", "opacity": 0.6,
    }},    
    {
        "selector": "edge",
        "style": {
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#64748b",
            "width": 1.5,
            "label": "data(label)",
            "font-size": "11px",
            "color": "#1e293b",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.95,
            "text-background-padding": "4px",
            "text-background-shape": "round-rectangle",
            "text-border-color": "#cbd5e1",
            "text-border-width": 1,
            "text-border-opacity": 0.5,
            "text-offset": 12,
            "text-rotation": "autorotate",
            "control-point-step-size": 80,
            "transition-property": "line-color, target-arrow-color, line-style",
            "transition-duration": "0.3s",
        },
    },
    {"selector": ".edge-done", "style": {
        "line-color": "#10b981", "target-arrow-color": "#10b981", "width": 2.5,
    }},
    {"selector": ".edge-pending", "style": {
        "line-color": "#cbd5e1", "target-arrow-color": "#cbd5e1",
    }},
    {"selector": ".edge-skipped", "style": {
        "line-color": "#d1d5db", "target-arrow-color": "#d1d5db",
        "line-style": "dashed", "opacity": 0.4,
    }},
    {"selector": ".edge-failed", "style": {
        "line-color": "#ef4444", "target-arrow-color": "#ef4444", "line-style": "dashed",
    }},
    {"selector": ".edge-cycle", "style": {
        "line-color": "#f59e0b", "target-arrow-color": "#f59e0b",
        "line-style": "dotted", "width": 2,
        "label": "data(full_label)", "font-size": "10px",
        "color": "#92400e",
        "text-background-color": "#fef3c7",
        "text-background-opacity": 0.95,
        "text-background-padding": "4px",
        "text-background-shape": "round-rectangle",
        "text-border-color": "#f59e0b",
        "text-border-width": 1,
    }},
    {"selector": "edge:hover", "style": {
        "line-color": "#1a73e8", "target-arrow-color": "#1a73e8", "width": 3,
        "label": "data(full_label)", "font-size": "11px", "color": "#1a73e8",
    }},
    {"selector": ":hover", "style": {"border-width": 3, "border-color": "#1a73e8"}},
    # ← NEW: 边类型样式
    {"selector": ".edge-soft", "style": {
        "line-style": "dashed", "opacity": 0.5, "width": 1.2,
    }},
    {"selector": ".edge-conditional", "style": {
        "line-style": "dotted", "width": 1.5,
    }},
    {"selector": ".edge-retry", "style": {
        "line-color": "#f59e0b", "target-arrow-color": "#f59e0b",
        "curve-style": "bezier", "width": 2,
        "line-style": "dashed",
    }},
    {"selector": ".edge-hard", "style": {
        # 默认实线，无需额外样式，但显式声明以防被覆盖
    }},
    {"selector": ":selected", "style": {
        "border-width": 4, "border-color": "#f59e0b"}},
]

#===已废弃（在main/assets/custom.css）另外实现===#
"""
PULSE_CSS = (
    "@keyframes pulse {"
    "0% { box-shadow: 0 0 0 0 rgba(59,130,246,0.5); }"
    "70% { box-shadow: 0 0 0 10px rgba(59,130,246,0); }"
    "100% { box-shadow: 0 0 0 0 rgba(59,130,246,0); }"
    "}"
)

PROGRESS_CSS = (
    "@keyframes stripe-slide {"
    "0% { background-position: 0 0; }"
    "100% { background-position: 30px 0; }"
    "}"
    ".indeterminate-bar {"
    "width: 100% !important;"
    "background: repeating-linear-gradient("
    "45deg, #3b82f6, #3b82f6 10px, #60a5fa 10px, #60a5fa 20px"
    ") !important;"
    "background-size: 30px 100% !important;"
    "animation: stripe-slide 0.8s linear infinite !important;"
    "border-radius: 6px !important;"
    "height: 100% !important;"
    "transition: none !important;"
    "}"
)
"""
#============================================#

# =============================================================================
# 12. 任务状态枚举（全模块共用）
# =============================================================================
TASK_STATUS = {
    "PENDING": 0,
    "RUNNING": 1,
    "SUCCESS": 2,
    "FAILED": 3,
    "TIMEOUT": 4,
    "SKIPPED": 5,
}

# =============================================================================
# 13. 边类型枚举（全模块共用）
# =============================================================================
EDGE_TYPE_HARD = "hard"
EDGE_TYPE_SOFT = "soft"
EDGE_TYPE_CONDITIONAL = "conditional"
EDGE_TYPE_RETRY = "retry"
DEFAULT_EDGE_TYPE = EDGE_TYPE_HARD

# 边类型 → CSS class（供 _edge_class 使用）
EDGE_TYPE_CSS = {
    EDGE_TYPE_HARD: "edge-hard",
    EDGE_TYPE_SOFT: "edge-soft",
    EDGE_TYPE_CONDITIONAL: "edge-conditional",
    EDGE_TYPE_RETRY: "edge-retry",
}

# 边类型 → 颜色（供 pyvis / matplotlib 渲染使用）
EDGE_TYPE_COLOR = {
    EDGE_TYPE_HARD: "#64748b",        # 灰色实线
    EDGE_TYPE_SOFT: "#94a3b8",        # 浅灰虚线
    EDGE_TYPE_CONDITIONAL: "#f59e0b",  # 橙色点划线
    EDGE_TYPE_RETRY: "#ef4444",       # 红色回退线
}

# 边类型 → line style（matplotlib / pydot）
EDGE_TYPE_STYLE = {
    EDGE_TYPE_HARD: "solid",
    EDGE_TYPE_SOFT: "dashed",
    EDGE_TYPE_CONDITIONAL: "dashdot",
    EDGE_TYPE_RETRY: "dotted",
}

# =============================================================================
# 14. 状态颜色与样式映射 (⚠️ 必须在 NODE_COLORS 之前定义)
# =============================================================================
# 状态 → 中文描述
STATUS_TEXT = {
    0: "⏳ 待处理",
    1: "🔄 进行中",
    2: "✅ 成功",
    3: "❌ 失败",
    4: "⏰ 超时",
    5: "⏭️ 已跳过",
}

# 状态 → 节点背景色
STATUS_COLOR = {
    0: "#fff3e0",
    1: "#e3f2fd",
    2: "#e8f5e9",
    3: "#ffebee",
    4: "#f3e5f5",
    5: "#f5f5f5",
}

# 状态 → 边框色
STATUS_BORDER = {
    0: "#f59e0b",
    1: "#3b82f6",
    2: "#10b981",
    3: "#ef4444",
    4: "#9c27b0",
    5: "#9e9e9e",
}

# 状态 → 图标
STATUS_ICONS = {
    0: "⏳",
    1: "🔄",
    2: "✅",
    3: "❌",
    4: "⏰",
    5: "⏭️",
}

NODE_TEXT_COLORS = {
    0: "#92400e",  # PENDING - 深棕
    1: "#1e3a8a",  # RUNNING - 深蓝
    2: "#14532d",  # SUCCESS - 深绿
    3: "#991b1b",  # FAILED  - 深红
    4: "#6b21a8",  # TIMEOUT - 深紫
    5: "#374151",  # SKIPPED - 深灰
}

NODE_COLORS = {
    status_key.lower(): (
        STATUS_COLOR[code],
        STATUS_BORDER[code],
        NODE_TEXT_COLORS.get(code, "#374151"),
    )
    for status_key, code in {
        "pending": 0, "running": 1, "done": 2,
        "failed": 3, "timeout": 4, "skipped": 5,
    }.items()
}
# 兼容旧键名
NODE_COLORS["completed"] = NODE_COLORS["done"]
NODE_COLORS["default"] = ("#ffffff", "#e5e7eb", "#374151")

# 边样式：基于边类型（而非状态）
EDGE_STYLES = {
    EDGE_TYPE_HARD: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_HARD],
        "width": RENDER_EDGE_WIDTH,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_HARD],
    },
    EDGE_TYPE_SOFT: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_SOFT],
        "width": RENDER_EDGE_WIDTH * 0.75,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_SOFT],
    },
    EDGE_TYPE_CONDITIONAL: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_CONDITIONAL],
        "width": RENDER_EDGE_WIDTH,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_CONDITIONAL],
    },
    EDGE_TYPE_RETRY: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_RETRY],
        "width": RENDER_EDGE_WIDTH,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_RETRY],
    },
}
DEFAULT_EDGE_STYLE = EDGE_STYLES[EDGE_TYPE_HARD]

# ✅ 模块级常量集合，避免每次调用重建 + O(1) 查找
VALID_EDGE_TYPES: FrozenSet[str] = frozenset({
    EDGE_TYPE_HARD,
    EDGE_TYPE_SOFT,
    EDGE_TYPE_CONDITIONAL,
    EDGE_TYPE_RETRY,
})
# 模块级常量
BAR_DONE = {"width": "100%", "height": "100%",
            "background": "linear-gradient(90deg, #10b981, #34d399)", "borderRadius": "6px"}
BAR_ERROR = {"width": "100%", "height": "100%",
             "background": "#ef4444", "borderRadius": "6px"}
BAR_LOADING = {"className": "indeterminate-bar",
               "width": "100%", "height": "100%"}

EMPTY_DAG = {"nodes": [], "edges": []}
EMPTY_STATES = {}
EMPTY_BAR_STYLE = {
    "width": "0%", "height": "100%",
    "background": "linear-gradient(90deg, #10b981, #34d399)",
    "borderRadius": "6px",
}
EMPTY_BAR_MINI_STYLE = {"width": "0%",
                        "height": "100%", "background": "transparent"}
MARKDOWN_PRE_STYLE = {"whiteSpace": "pre-wrap",
                      "fontSize": "14px", "lineHeight": "1.8"}
# 样式常量提取到回调外部，避免每次触发都重新创建字典
HINT_BASE_STYLE = {"fontSize": "13px",
                   "marginTop": "8px", "fontStyle": "italic"}
HINT_STYLES = {
    "loading":  {**HINT_BASE_STYLE, "color": "#f59e0b", "fontWeight": "bold"},
    "ready":    {**HINT_BASE_STYLE, "color": "#10b981", "fontWeight": "bold"},
    "running":  {**HINT_BASE_STYLE, "color": "#10b981", "fontWeight": "bold"},
    "done":     {**HINT_BASE_STYLE, "color": "#10b981"},
    "skipped":  {**HINT_BASE_STYLE, "color": "#9ca3af"},
    "failed":   {**HINT_BASE_STYLE, "color": "#ef4444"},
    "pending":  {**HINT_BASE_STYLE, "color": "#f59e0b"},
}

# ⭐ 提取基础样式常量
BASE_BTN_STYLE = {
    "border": "none",
    "padding": "8px 16px",
    "borderRadius": "6px",
    "cursor": "pointer",
    "marginRight": "6px",
    "fontWeight": "bold",
    "fontSize": "13px",
}

# ── 按钮样式（派生自 BASE_BTN_STYLE） ──
BUTTON_STYLE_PRIMARY = {
    **BASE_BTN_STYLE,
    "backgroundColor": "#1a73e8",
    "color": "white",
}

BUTTON_STYLE_DANGER = {
    **BASE_BTN_STYLE,
    "backgroundColor": "#ef4444",
    "color": "white",
}

BUTTON_STYLE_SECONDARY = {
    **BASE_BTN_STYLE,
    "backgroundColor": "#e2e8f0",
    "color": "#475569",
}

# ── 章节标题样式 ──
SECTION_HEADER_STYLE = {
    "fontSize": "14px",
    "fontWeight": "bold",
    "color": "#1e293b",
    "marginBottom": "8px",
}

# ── 缩放工具栏按钮样式 ──
ZOOM_TOOLBAR_STYLE = {
    "backgroundColor": "white",
    "border": "1px solid #d1d5db",
    "borderRadius": "6px",
    "padding": "6px 12px",
    "cursor": "pointer",
    "fontSize": "13px",
    "color": "#475569",
}

# 状态颜色映射
STATE_COLORS = {
    "done": ("#10b981", "white"),
    "skipped": ("#6366f1", "white"),
    "failed": ("#ef4444", "white"),
    "running": ("#f59e0b", "white"),
    "pending": ("#e2e8f0", "#475569"),
    "blocked": ("#94a3b8", "white"),
}

STATE_LABELS = {
    "done": "✅ 已完成", "skipped": "⏭️ 已跳过",
    "failed": "❌ 做不到", "running": "🔄 进行中",
    "pending": "⏳ 等待中", "blocked": "🔒 被阻塞",
}

# ══════════════════════════════════════════════════
#  兼容旧名（下游模块如有直接引用不会报错）
# ══════════════════════════════════════════════════
NODE_STATUS_COLORS = STATUS_COLOR
NODE_STATUS_BORDERS = STATUS_BORDER
NODE_STATUS_ICONS = STATUS_ICONS

# ══════════════════════════════════════════════════
#  节点样式（从 config 动态生成，不再硬编码）
# ══════════════════════════════════════════════════
NODE_STYLES: Dict[int, Dict[str, str]] = {
    code: {
        "bg": STATUS_COLOR[code],
        "border": STATUS_BORDER[code],
        "icon": STATUS_ICONS[code],
    }
    for code in STATUS_COLOR
}
# 允许手动干预（done/skip/fail）的节点状态集合
NODE_OPERABLE_STATES = {"pending", "running", "failed"}

# ✅ P0-2 修复：后端状态码 → 前端节点状态映射（与 TASK_STATUS 对齐）
NODE_STATUS_CODE_MAP = {
    0: "pending",
    1: "running",
    2: "done",
    3: "failed",
    4: "timeout",     # ✅ 修正：原为 "skipped"，与 TASK_STATUS 定义冲突
    5: "skipped",     # ✅ 补齐：原缺失，导致 status_code=5 时前端拿到 undefined
}

# 节点操作配置：定义每种操作的下游行为和提示文案
NODE_ACTION_CONFIG = {
    "done": {
        "unlock_downstream": True,
        "label": "完成",
        "icon": "✅",
    },
    "skipped": {
        "unlock_downstream": False,  # ← 关键：跳过不解锁下游
        "label": "跳过",
        "icon": "⏭️",
    },
    "failed": {
        "unlock_downstream": False,
        "label": "失败",
        "icon": "❌",
    },
}

# 历史任务下拉框 label 最大长度
HISTORY_LABEL_MAX_LEN = 30

# 端口合法范围常量
VALID_PORT_RANGE = range(1, 65536)


# ── 图实例配置注册表 ──
# 新增图表只需在此添加配置，无需复制 JS 代码
GRAPH_CONFIGS = {
    "main": {
        "element_id": "flowchart",
        "fit_btn_id": "fit-btn",
        "zoom_in_btn_id": "zoom-in-btn",
        "zoom_out_btn_id": "zoom-out-btn",
        "zoom_slider_id": "zoom-slider",
        "layout_options": {
            "name": "dagre",
            "rankDir": "TB",
            "nodeSep": 300,
            "rankSep": 450,
            "edgeSep": 80,
            "padding": 80,
        },
    },
    "history": {
        "element_id": "history-flowchart",
        "fit_btn_id": "history-fit-btn",
        "zoom_in_btn_id": "history-zoom-in-btn",
        "zoom_out_btn_id": "history-zoom-out-btn",
        "zoom_slider_id": "history-zoom-slider",
        "layout_options": {
            "name": "dagre",
            "rankDir": "TB",
            "nodeSep": 250,
            "rankSep": 380,
            "edgeSep": 60,
            "padding": 60,
        },
    },
}

# ── 通用 JS 函数模板（字符串模板，运行时注入配置） ──

FIT_JS_TEMPLATE = """
function(n_clicks) {{
    if (!n_clicks) return window.dash_clientside.no_update;
    var el = document.getElementById('{element_id}');
    // ⚠️ _cyRef 是 dash-cytoscape 非公开 API，版本升级时需验证
    if (!el || !el._cyRef) return window.dash_clientside.no_update;
    var cy = el._cyRef;
    var layoutOpts = {layout_json};
    
    // 使用 layoutstop 事件替代硬编码 setTimeout，确保布局完成后才 fit
    cy.once('layoutstop', function() {{
        cy.fit(cy.elements(), 80);
    }});
    cy.layout(layoutOpts).run();
    return window.dash_clientside.no_update;
}}
"""

ZOOM_BTN_JS_TEMPLATE = """
function(n_clicks_in, n_clicks_out) {{
    var ctx = window.dash_clientside.callback_context;
    if (!ctx || !ctx.triggered || ctx.triggered.length === 0) {{
        return window.dash_clientside.no_update;
    }}
    var el = document.getElementById('{element_id}');
    if (!el || !el._cyRef) return 0.6;
    var cy = el._cyRef;
    
    var cur = cy.zoom() || 0.6;
    var prop_id = ctx.triggered[0].prop_id || '';
    var ZOOM_STEP = 0.2;
    var ZOOM_MIN = 0.2;
    var ZOOM_MAX = 4.0;
    
    if (prop_id.indexOf('{zoom_in_btn_id}') !== -1) {{
        cy.zoom(Math.min(ZOOM_MAX, cur + ZOOM_STEP));
    }} else if (prop_id.indexOf('{zoom_out_btn_id}') !== -1) {{
        cy.zoom(Math.max(ZOOM_MIN, cur - ZOOM_STEP));
    }}
    return cy.zoom() || 0.6;
}}
"""

ZOOM_SLIDER_JS_TEMPLATE = """
function(val) {{
    if (val == null) return window.dash_clientside.no_update;
    var el = document.getElementById('{element_id}');
    if (!el || !el._cyRef) return window.dash_clientside.no_update;
    el._cyRef.zoom(val);
    return window.dash_clientside.no_update;
}}
"""

# ══════════════════════════════════════════════════
#  边样式（根据边类型返回 pyvis 参数）
# ══════════════════════════════════════════════════
EDGE_DASH_MAP: Dict[str, List[int]] = {
    "dashed":  [10, 5],
    "dashdot": [15, 5, 5, 5],
    "dotted":  [3, 3],
}

# =============================================================================
# 15. Pattern & Security (正则与安全脱敏)
# =============================================================================
MAX_INPUT_LENGTH = 2000
# 缓存上限：超过此大小的输入跳过缓存，防止内存膨胀
MAX_CACHE_INPUT_BYTES = 10 * 1024 * 1024  # 10 MB
THINK_RE = re.compile(r'<' + r'think>.*?<' + r'/think>', re.DOTALL)

# ✅ P1-3 修复：所有 "词 + 分隔符" 型 pattern 加 (?<![a-zA-Z_]) 前缀，
#    避免 my_password=xxx 被误伤。
SENSITIVE_PATTERNS_CONFIG = [
    {"pattern": r'sk-[a-zA-Z0-9]{20,}', "replacement": '[REDACTED_API_KEY]'},
    {"pattern": r'AKIA[A-Z0-9]{16,}', "replacement": '[REDACTED_AWS_KEY]'},
    {"pattern": r'ghp_[a-zA-Z0-9]{36,}',
        "replacement": '[REDACTED_GITHUB_TOKEN]'},
    {"pattern": r'xox[bpr]-[a-zA-Z0-9-]+',
        "replacement": '[REDACTED_SLACK_TOKEN]'},
    {"pattern": (r'(?<![a-zA-Z_])password\s*[:=]\s*\S{1,200}', re.IGNORECASE),
     "replacement": 'password=[REDACTED]'},
    {"pattern": (r'(?<![a-zA-Z_])token\s*[:=]\s*\S{1,200}', re.IGNORECASE),
     "replacement": 'token=[REDACTED]'},
    {"pattern": (r'(?<![a-zA-Z_])api_key\s*[:=]\s*\S{1,200}', re.IGNORECASE),
     "replacement": 'api_key=[REDACTED]'},
    {"pattern": (r'(?<![a-zA-Z_])secret\s*[:=]\s*\S{1,200}', re.IGNORECASE),
     "replacement": 'secret=[REDACTED]'},
    {"pattern": r'\b1[3-9]\d{9}\b', "replacement": '[REDACTED_PHONE]'},
    {"pattern": r'\b\d{17}[\dXx]\b', "replacement": '[REDACTED_ID_CARD]'},
]

# nodes.py 初始化时编译 (此处预编译以提升性能)
SENSITIVE_PATTERNS = [
    (re.compile(cfg["pattern"]) if isinstance(cfg["pattern"], str) else re.compile(*cfg["pattern"]),
     cfg["replacement"])
    for cfg in SENSITIVE_PATTERNS_CONFIG
]

BLOCKED_WORDS: Set[str] = set()  # TODO: 从配置文件/数据库加载违禁词库
SESSION_TTL = 3600  # 1小时

# =============================================================================
# 16. 常量定义 (Constants)
# =============================================================================
# 意图枚举白名单（frozenset 不可变，O(1) 查找，防止运行时被意外修改）
# ✅ [Fix] 将 "consultation" 加入白名单
# 否则 Post-check 设置的 category 会被 _validate_intent 二次降级为 "other"
VALID_INTENT_CATEGORIES: FrozenSet[str] = frozenset({
    "cooking", "engineering", "logistics",
    "learning", "life_admin", "creative",
    "consultation",  # ← 新增：显式拒绝规划时的咨询类意图
    "other",
})

VALID_INTENT_COMPLEXITIES: FrozenSet[str] = frozenset(
    {"simple", "medium", "complex"})

# Mock 回复前缀，便于下游管线识别并跳过 JSON 清洗
MOCK_RESPONSE_PREFIX: str = "[MOCK_LLM_RESPONSE]"

# 预编译正则：匹配未被替换的 $var 或 ${var}，排除 $$ 转义
UNREPLACED_PATTERN = re.compile(r'\$\{(\w+)\}|(?<!\$)\$(\w+)')

# ══════════════════════════════════════════════════
#  ✅ 视图分层：面向用户的字段白名单
# ══════════════════════════════════════════════════
USER_VISIBLE_NODE_FIELDS = {"id", "name", "details", "status", "result"}

HINT_TEMPLATES = {
    "running": "✅ 已选中节点 {nid}，现在可以「开始执行」或「跳过/做不到」",
    "done": "✅ 节点 {nid} 已完成",
    "skipped": "⏭️ 节点 {nid} 已跳过",
    "failed": "❌ 节点 {nid} 标记为做不到",
    "pending": "⏳ 节点 {nid} 等待前置条件完成，暂时无法操作",
}

# =============================================================================
# 17. Prompt 模板库 (Prompt Templates)
# =============================================================================
# 保持原版 Template 语法 ($var)

# ✅ P1-1 修复：category 白名单补上 "consultation"
#    否则 LLM 永远不会输出 consultation，Post-check 的硬编码分流会掩盖真实的意图识别。
INTENT_PROMPT = Template("""你是一个高精度的任务意图路由器。
分析用户输入，判断是否需要启动 DAG 任务规划引擎。

<output_format>
你必须且只能返回一个合法 JSON 对象，不要包含任何其他文字、Markdown、注释或解释。
JSON 必须严格符合以下 schema：
{
  "needs_planning": boolean,
  "category": "cooking" | "engineering" | "logistics" | "learning" | "life_admin" | "creative" | "consultation" | "other",
  "summary": "string (≤30字，一句话概括核心意图)",
  "complexity": "simple" | "medium" | "complex"
}
所有字段必须存在且不为 null。
⚠️ 输出格式硬约束：你必须且只能返回一个合法 JSON 对象。
禁止包含任何思考过程、解释说明、Markdown 标记或代码块围栏。
直接以 { 开头，以 } 结尾。
</output_format>

<decision_tree>
严格按优先级执行，命中即停止，禁止跨级覆盖：

P0[显式否决 - 最高优先级，绝对不可覆盖] → needs_planning = false
  只要输入含以下任一语义，无论内容多复杂、枚举维度多少、是否提及"规划助手"，一律 false：
  "不需要规划/画图/流程/步骤""直接回答/教/告诉""简单说""别拆解""只要答案""不要流程图"
  ⚠️ 此规则优先级高于 P0.5/P1/P2/P3 的所有条款，包括"信息密度兜底"和"显式调用规划能力"。
  当 P0 与其他规则冲突时，无条件服从 P0。

P0.5[显式调用规划能力] → needs_planning = true
  输入中显式提及"任务规划""规划助手""帮我规划""制定方案""决策流程""明示步骤"等
  直接调用本系统规划能力的表述。
  ⚠️ 前提：未触发 P0。若同时存在 P0 否决词，以 P0 为准。

P1[纯知识/信息检索] → needs_planning = false
  - 询问事实、定义、历史、原理
  - 询问方法但未要求执行（"怎么做东坡肉" ≠ "帮我做东坡肉"）
  - 单轮可完整回答的问答、翻译、改写、闲聊

P2[可执行多步骤任务] → needs_planning = true
  必须同时满足：
  ① 用户有明确的"执行/完成"意图（非仅"了解/教我"）
  ② 需分解为 ≥2 个有序子步骤才能交付结果
  ③ 未触发 P0 / P0.5
  典型：部署系统、写长篇报告、旅行规划、完整菜谱实操、装修流程
  ℹ️ 信息密度参考（非强制触发）：若输入包含 ≥3 个并列枚举维度，
     可作为判断"单轮无法覆盖"的辅助依据，但仍须先通过 P0 检查。
     若 P0 已命中，此条不生效。

P3[兜底] → needs_planning = false
  不确定时倾向 false。"怎么做X"默认 false，除非追加"帮我一步步做/给我完整执行方案"。
</decision_tree>

<examples>
输入："steam上游戏价格状态有11种，购买意愿4种，资金状况5种，请直接教我如何在这些组合下做到购买利益最大化，不要生成流程图"
输出：{"needs_planning": false, "category": "life_admin", "summary": "Steam购买策略咨询（用户拒绝规划）", "complexity": "simple"}

输入："steam上的游戏推荐，请直接回答我，不需要生成规划图"
输出：{"needs_planning": false, "category": "other", "summary": "Steam游戏推荐（用户拒绝规划）", "complexity": "simple"}

输入："家庭版懒人东坡肉的详细做法，要完整流程和选材标准"
输出：{"needs_planning": true, "category": "cooking", "summary": "制作家庭版懒人东坡肉完整流程", "complexity": "medium"}

输入："东坡肉怎么做才好吃？"
输出：{"needs_planning": false, "category": "cooking", "summary": "查询东坡肉烹饪技巧", "complexity": "simple"}

输入："帮我搭建一个K8s集群，要生产级配置"
输出：{"needs_planning": true, "category": "engineering", "summary": "搭建生产级K8s集群", "complexity": "complex"}

输入："steam游戏打折规律是什么？"
输出：{"needs_planning": false, "category": "life_admin", "summary": "查询Steam打折规律", "complexity": "simple"}

输入："steam上游戏价格状态有11种，购买意愿4种，资金状况5种，请任务规划助手明示如何在这些组合下做到购买利益最大化"
输出：{"needs_planning": true, "category": "life_admin", "summary": "Steam多场景购买决策方案", "complexity": "complex"}

输入："帮我咨询一下租房合同里有哪些坑"
输出：{"needs_planning": false, "category": "consultation", "summary": "租房合同风险咨询", "complexity": "simple"}

输入："你好呀"
输出：{"needs_planning": false, "category": "other", "summary": "用户问候", "complexity": "simple"}
</examples>

<user_input>
${user_input}
</user_input>""")

PLANNER_PROMPT = Template("""你是一个专业的任务规划架构师。
将用户需求分解为原子化、可验证的 DAG 节点。

<output_format>
你必须且只能返回一个合法 JSON 对象，不要包含任何其他文字、Markdown、注释或解释。
JSON 必须严格符合以下 schema：
{
  "task_name": "string (≤20字)",
  "description": "string (任务整体描述)",
  "nodes": [
    {"id": 1, "name": "动词+宾语(≤15字)", "detail": "具体操作说明，含关键参数和验收标准"}
  ],
  "edges": [
    {"from": 1, "to": 2, "label": "依赖条件(可选)"}
  ]
}
- id 从 1 开始连续编号
- nodes 不可为空数组
- edges 中 from / to 必须引用已存在的 node id
</output_format>

<planning_principles>
1. 原子性：一个节点 = 一个可独立验证的动作
   ✅ "选购五花肉" | ❌ "买肉并切块"

2. 并行优先：无依赖节点必须并行，禁止强行线性串联
   ✅ 选肉 ∥ 备调料 → 同时汇入"腌制"节点

3. 粒度控制：
   - simple: 3~5 节点 | medium: 5~10 | complex: 10~20
   - 任务本身简单时不要为凑数过度拆解

4. 领域适配：
   - 烹饪：选材∥备料 → 预处理 → 烹饪 → 收尾
   - 工程：调研 → 设计 → 实现 → 测试 → 部署
   - 学习：资料收集∥大纲制定 → 分模块学习 → 总结输出
</planning_principles>

<context>
任务类别：${category}（若无则填"通用"）
意图摘要：${summary}（若无则从 user_input 中提取核心动词+宾语）
</context>

<user_input>
${user_input}
</user_input>""")

NODE_REFINE_PROMPT = Template("""你是一个任务执行细节专家。
为单个节点补充可直接执行的行动规范。

<output_format>
你必须且只能返回一个合法 JSON 对象，不要包含任何其他文字、Markdown、注释或解释。
JSON 必须严格符合以下 schema：
{
  "name": "string (动词+宾语，≤20字)",
  "detail": "string (分步操作逻辑，用\\n分隔，含量化参数、判断标准、避坑指南)",
  "meta": {
    "preconditions": ["具体前置状态，非'前序节点完成'"],
    "postconditions": ["可验证的交付物/状态变化"],
    "retry_policy": "重试次数/间隔/放弃条件"
  }
}
所有字段必须存在且不为 null。
</output_format>

<refinement_standards>
1. detail 必须可盲执行：
   - 烹饪：用量(g/ml)、火候、时间(min)、状态标志("筷子可插入")
   - 工程：命令、参数、预期输出、错误码处理
   - 生活：渠道、联系方式、判断阈值

2. preconditions/postconditions 必须具体：
   ✅ "五花肉已洗净切4cm方块，共500g"
   ❌ "肉已准备好" / "任务完成"

3. retry_policy 必须可操作：
   ✅ "网络超时重试3次，间隔5s；API返回403则终止并报错"
   ❌ "失败就重试"
</refinement_standards>

<context>
任务领域：${category}
</context>

<node_info>
${node_json}
</node_info>

<user_input>
${user_input}
</user_input>""")

EXECUTE_NODE_PROMPT = Template("""你是任务执行引擎。对以下节点做执行推理。

【全局目标】${user_input}
【领域】${category}
【当前节点】${name}
【操作细节】${detail}
【前置条件】${preconditions}
【预期结果】${postconditions}

严格按 JSON 返回：
{"success": true, "result": "...", "notes": "..."}""")

# =============================================================================
# 18. 正则模式 (Regex Patterns)
# =============================================================================
SKIP_PLANNING_PATTERNS = [
    re.compile(r"不[需要]?(?:要|用).{0,6}(?:流程图|DAG|图表|拆解|步骤|规划)"),
    re.compile(
        r"(?:直接|只要|请).{0,8}(?:告诉|教|回答|给出|说明).{0,10}(?:方法|策略|建议|结论|做法|答案)"),
    re.compile(r"(?:跳过|别|莫).{0,4}(?:规划|画图|拆解|流程)"),
    re.compile(r"不[需要]?(?:要|用).{0,4}画.{0,4}图"),
]

# =============================================================================
# 19. 异步并发控制
# =============================================================================
LLM_MAX_CONCURRENT = _env("LLM_MAX_CONCURRENT", 10, int)   # 同时最多进行的 LLM 调用数
LLM_CONNECTION_POOL_SIZE = _env("LLM_CONNECTION_POOL_SIZE", 20, int)  # 连接池大小（httpx 内部）

# GOOOD addon
WERKZEUG_RUN_MAIN = _env("WERKZEUG_RUN_MAIN", True, bool)

