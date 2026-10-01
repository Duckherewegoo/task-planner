"""cytoscape_styles.py — Cytoscape 样式表（原 config.py 第 11 节）"""
from typing import Any, Dict, List


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

