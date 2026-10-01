"""
constants.py — 全项目共享的代码常量/枚举。
不是配置，不接受运行时修改，不进 cog。
"""
from typing import Dict, FrozenSet, List

# ══════════════════════════════════════════════════
#  任务状态
# ══════════════════════════════════════════════════
TASK_STATUS = {
    "PENDING": 0,
    "RUNNING": 1,
    "SUCCESS": 2,
    "FAILED": 3,
    "TIMEOUT": 4,
    "SKIPPED": 5,
}

# ══════════════════════════════════════════════════
#  边类型
# ══════════════════════════════════════════════════
EDGE_TYPE_HARD = "hard"
EDGE_TYPE_SOFT = "soft"
EDGE_TYPE_CONDITIONAL = "conditional"
EDGE_TYPE_RETRY = "retry"
DEFAULT_EDGE_TYPE = EDGE_TYPE_HARD

EDGE_TYPE_CSS = {
    EDGE_TYPE_HARD: "edge-hard",
    EDGE_TYPE_SOFT: "edge-soft",
    EDGE_TYPE_CONDITIONAL: "edge-conditional",
    EDGE_TYPE_RETRY: "edge-retry",
}

EDGE_TYPE_COLOR = {
    EDGE_TYPE_HARD: "#64748b",
    EDGE_TYPE_SOFT: "#94a3b8",
    EDGE_TYPE_CONDITIONAL: "#f59e0b",
    EDGE_TYPE_RETRY: "#ef4444",
}

EDGE_TYPE_STYLE = {
    EDGE_TYPE_HARD: "solid",
    EDGE_TYPE_SOFT: "dashed",
    EDGE_TYPE_CONDITIONAL: "dashdot",
    EDGE_TYPE_RETRY: "dotted",
}

EDGE_DASH_MAP: Dict[str, List[int]] = {
    "dashed":  [10, 5],
    "dashdot": [15, 5, 5, 5],
    "dotted":  [3, 3],
}

VALID_EDGE_TYPES: FrozenSet[str] = frozenset({
    EDGE_TYPE_HARD, EDGE_TYPE_SOFT,
    EDGE_TYPE_CONDITIONAL, EDGE_TYPE_RETRY,
})

# ══════════════════════════════════════════════════
#  状态颜色与样式映射
# ══════════════════════════════════════════════════
STATUS_TEXT = {
    0: "⏳ 待处理",
    1: "🔄 进行中",
    2: "✅ 成功",
    3: "❌ 失败",
    4: "⏰ 超时",
    5: "⏭️ 已跳过",
}

STATUS_COLOR = {
    0: "#fff3e0",
    1: "#e3f2fd",
    2: "#e8f5e9",
    3: "#ffebee",
    4: "#f3e5f5",
    5: "#f5f5f5",
}

STATUS_BORDER = {
    0: "#f59e0b",
    1: "#3b82f6",
    2: "#10b981",
    3: "#ef4444",
    4: "#9c27b0",
    5: "#9e9e9e",
}

STATUS_ICONS = {
    0: "⏳",
    1: "🔄",
    2: "✅",
    3: "❌",
    4: "⏰",
    5: "⏭️",
}

NODE_TEXT_COLORS = {
    0: "#92400e",
    1: "#1e3a8a",
    2: "#14532d",
    3: "#991b1b",
    4: "#6b21a8",
    5: "#374151",
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
NODE_COLORS["completed"] = NODE_COLORS["done"]
NODE_COLORS["default"] = ("#ffffff", "#e5e7eb", "#374151")

# ══════════════════════════════════════════════════
#  边样式（基于边类型）
# ══════════════════════════════════════════════════
_RENDER_EDGE_WIDTH_DEFAULT = 2.0  # 与 cog 中的 RENDER_EDGE_WIDTH 默认值保持一致

EDGE_STYLES = {
    EDGE_TYPE_HARD: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_HARD],
        "width": _RENDER_EDGE_WIDTH_DEFAULT,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_HARD],
    },
    EDGE_TYPE_SOFT: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_SOFT],
        "width": _RENDER_EDGE_WIDTH_DEFAULT * 0.75,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_SOFT],
    },
    EDGE_TYPE_CONDITIONAL: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_CONDITIONAL],
        "width": _RENDER_EDGE_WIDTH_DEFAULT,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_CONDITIONAL],
    },
    EDGE_TYPE_RETRY: {
        "color": EDGE_TYPE_COLOR[EDGE_TYPE_RETRY],
        "width": _RENDER_EDGE_WIDTH_DEFAULT,
        "style": EDGE_TYPE_STYLE[EDGE_TYPE_RETRY],
    },
}
DEFAULT_EDGE_STYLE = EDGE_STYLES[EDGE_TYPE_HARD]

# ══════════════════════════════════════════════════
#  节点样式
# ══════════════════════════════════════════════════
NODE_STYLES: Dict[int, Dict[str, str]] = {
    code: {
        "bg": STATUS_COLOR[code],
        "border": STATUS_BORDER[code],
        "icon": STATUS_ICONS[code],
    }
    for code in STATUS_COLOR
}

NODE_OPERABLE_STATES = {"pending", "running", "failed"}

NODE_STATUS_CODE_MAP = {
    0: "pending",
    1: "running",
    2: "done",
    3: "failed",
    4: "timeout",
    5: "skipped",
}

NODE_ACTION_CONFIG = {
    "done":    {"unlock_downstream": True,  "label": "完成", "icon": "✅"},
    "skipped": {"unlock_downstream": False, "label": "跳过", "icon": "⏭️"},
    "failed":  {"unlock_downstream": False, "label": "失败", "icon": "❌"},
}

# ══════════════════════════════════════════════════
#  兼容旧名
# ══════════════════════════════════════════════════
NODE_STATUS_COLORS = STATUS_COLOR
NODE_STATUS_BORDERS = STATUS_BORDER
NODE_STATUS_ICONS = STATUS_ICONS

# ══════════════════════════════════════════════════
#  UI 状态（前端节点展示用）
# ══════════════════════════════════════════════════
STATE_COLORS = {
    "done":    ("#10b981", "white"),
    "skipped": ("#6366f1", "white"),
    "failed":  ("#ef4444", "white"),
    "running": ("#f59e0b", "white"),
    "pending": ("#e2e8f0", "#475569"),
    "blocked": ("#94a3b8", "white"),
}

STATE_LABELS = {
    "done":    "✅ 已完成",
    "skipped": "⏭️ 已跳过",
    "failed":  "❌ 做不到",
    "running": "🔄 进行中",
    "pending": "⏳ 等待中",
    "blocked": "🔒 被阻塞",
}

HINT_TEMPLATES = {
    "running": "✅ 已选中节点 {nid}，现在可以「开始执行」或「跳过/做不到」",
    "done":    "✅ 节点 {nid} 已完成",
    "skipped": "⏭️ 节点 {nid} 已跳过",
    "failed":  "❌ 节点 {nid} 标记为做不到",
    "pending": "⏳ 节点 {nid} 等待前置条件完成，暂时无法操作",
}

# ══════════════════════════════════════════════════
#  意图
# ══════════════════════════════════════════════════
VALID_INTENT_CATEGORIES: FrozenSet[str] = frozenset({
    "cooking", "engineering", "logistics",
    "learning", "life_admin", "creative",
    "consultation", "other",
})

VALID_INTENT_COMPLEXITIES: FrozenSet[str] = frozenset(
    {"simple", "medium", "complex"}
)

# ══════════════════════════════════════════════════
#  其它
# ══════════════════════════════════════════════════
USER_VISIBLE_NODE_FIELDS = {"id", "name", "details", "status", "result"}
HISTORY_LABEL_MAX_LEN = 30
VALID_PORT_RANGE = range(1, 65536)

SUPPORTED_EXPORT_FORMATS = ["svg", "png", "pdf", "dot", "html", "json"]

FONT_FACE = (
    "'Noto Sans CJK SC', 'WenQuanYi Zen Hei', 'Microsoft YaHei', "
    "'SimHei', 'DejaVu Sans', Arial, sans-serif"
)
