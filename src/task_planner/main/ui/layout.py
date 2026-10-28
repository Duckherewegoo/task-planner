"""layout.py — Dash 页面布局。

本次变更（仅 3 处）：
  1. 文件顶部：import build_config_panel 的懒加载包装
  2. build_layout() 最外层 children 头部：追加 dcc.Location
  3. build_layout() 最外层 children 尾部：追加悬浮按钮 + 侧滑抽屉
原有所有 children 一行不动，最小侵入。
"""

import dash_cytoscape as cyto
from dash import dcc, html

from task_planner.infrastructure.assets.cytoscape_styles import CYTO_STYLESHEET
from task_planner.infrastructure.ui_styles import (
    BUTTON_STYLE_DANGER,
    BUTTON_STYLE_PRIMARY,
    BUTTON_STYLE_SECONDARY,
    SECTION_HEADER_STYLE,
    ZOOM_TOOLBAR_STYLE,
)

# ═══════════════════════════════════════════════════════════════════
# 新增（改动 1）：配置抽屉的触发按钮与侧滑面板
# ═══════════════════════════════════════════════════════════════════
# 样式常量拆开声明——回调里只改 display 字段，避免完整 style 反复刷屏

_CONFIG_TRIGGER_STYLE = {
    "position": "fixed",
    "right": "24px",
    "bottom": "24px",
    "width": "56px",
    "height": "56px",
    "display": "flex",
    "alignItems": "center",
    "justifyContent": "center",
    "fontSize": "24px",
    "color": "white",
    "backgroundColor": "#1a73e8",
    "borderRadius": "50%",
    "boxShadow": "0 4px 16px rgba(26,115,232,0.4)",
    "textDecoration": "none",
    "zIndex": 1050,
    "transition": "transform 0.15s ease",
}

_CONFIG_OVERLAY_STYLE = {
    "position": "fixed",
    "top": 0,
    "left": 0,
    "width": "100vw",
    "height": "100vh",
    "backgroundColor": "rgba(15,23,42,0.4)",
    "zIndex": 9998,
    "display": "none",  # 初始关闭，由 URL 回调切换
}

_CONFIG_DRAWER_STYLE = {
    "position": "fixed",
    "top": 0,
    "right": 0,
    "width": "560px",
    "maxWidth": "95vw",
    "height": "100vh",
    "backgroundColor": "white",
    "boxShadow": "-4px 0 24px rgba(15,23,42,0.15)",
    "zIndex": 9999,
    "overflowY": "auto",
    "display": "none",  # 初始关闭，由 URL 回调切换
}


def _build_config_trigger() -> dcc.Link:
    """悬浮触发按钮。

    用 dcc.Link 而非 html.Button —— dcc.Link 渲染为 <a>，点击时 Dash 拦截
    默认行为、只改 dcc.Location.pathname，不刷新页面（客户端路由）。
    这是「无缝」的本质：主页面状态零丢失。
    """
    return dcc.Link(
        "⚙️",
        id="open-config-trigger",
        href="/config",
        title="打开配置工具",
        style=_CONFIG_TRIGGER_STYLE,
    )


def _build_config_drawer() -> html.Div:
    """URL 驱动的侧滑配置抽屉。

    结构：
      - 遮罩层（config-overlay）：display 由回调控制
      - 面板（config-drawer-panel）：display 由回调控制
        - 头部：标题 + 关闭按钮（dcc.Link href="/"）
        - 主体：build_config_panel()（复用 config_ui 的布局）
    """
    # 懒加载：build_form 会扫描 hub.snapshot()，模块级 import 会拉长启动时间
    from task_planner.config_ui.layout import build_config_panel

    return html.Div(
        [
            html.Div(id="config-overlay", style=_CONFIG_OVERLAY_STYLE),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(
                                "⚙️ Task Planner 配置",
                                style={
                                    "fontSize": "18px",
                                    "fontWeight": "bold",
                                    "color": "#1e293b",
                                },
                            ),
                            dcc.Link(
                                "✕",
                                id="close-config-trigger",
                                href="/",
                                title="关闭配置",
                                style={
                                    "fontSize": "22px",
                                    "color": "#64748b",
                                    "textDecoration": "none",
                                    "padding": "0 10px",
                                    "cursor": "pointer",
                                },
                            ),
                        ],
                        style={
                            "display": "flex",
                            "justifyContent": "space-between",
                            "alignItems": "center",
                            "padding": "16px 20px",
                            "borderBottom": "1px solid #e2e8f0",
                        },
                    ),
                    html.Div(
                        build_config_panel(),
                        style={"padding": "20px"},
                    ),
                ],
                id="config-drawer-panel",
                style=_CONFIG_DRAWER_STYLE,
            ),
        ]
    )


# ── 布局 ──
def build_layout() -> html.Div:
    """主页面布局。

    结构（自上而下六个分区）：
      ① 全局状态层  — dcc.Location / Interval / Stores / Download（不可见）
      ② 导出工具栏  — 右上角浮动卡片（图片 / 数据导出）
      ③ 页头区      — 标题、副标题、全局状态文本
      ④ 进度条      — 顶部居中细长条
      ⑤ 主标签页    — 新建任务 / 历史记录
      ⑥ 配置抽屉    — 悬浮按钮 + URL 驱动的侧滑面板（覆盖层）

    约定：
      - dcc.Location 必须位于 children 最外层第一个（Dash 的硬约束）
      - ⑥ 的 trigger / drawer 必须位于 html.Div children 最后（保证浮层
        渲染在页面之上，不受 DOM 顺序影响 z-index 竞争）
    """
    return html.Div(
        [
            # ═══════════════════════════════════════════════════════════
            # ① 全局状态层（不可见，只承载数据 / 触发回调）
            # ═══════════════════════════════════════════════════════════
            # URL 状态源：驱动配置抽屉开关，必须在 children 最外层第一个
            dcc.Location(id="app-url", refresh=False),
            # 全局定时器：stream 轮询触发
            dcc.Interval(id="stream-interval", interval=1000, disabled=True, n_intervals=0),
            # 会话 / DAG / 选中节点等运行时状态
            dcc.Store(id="thread-id-store", data=""),
            dcc.Store(id="stream-active", data=False),
            dcc.Store(id="node-states-store", data={}),
            dcc.Store(id="dag-store", data={"nodes": [], "edges": []}),
            dcc.Store(id="selected-node-store", data=""),
            dcc.Store(id="task-start-time", data=None),
            dcc.Store(id="history-replan-trigger", data=0),
            # 下载触发器：由导出按钮回调写入
            dcc.Download(id="export-download"),
            # ═══════════════════════════════════════════════════════════
            # ② 导出工具栏（右上角浮动卡片）
            # ═══════════════════════════════════════════════════════════
            html.Div(
                [
                    # 图片导出组
                    html.Div(
                        [
                            html.Span(
                                "📤 导出图像：",
                                style={
                                    "fontSize": "12px",
                                    "fontWeight": "bold",
                                    "marginRight": "6px",
                                    "whiteSpace": "nowrap",
                                },
                            ),
                            dcc.RadioItems(
                                id="image-format",
                                options=[
                                    {"label": "PNG", "value": "png"},
                                    {"label": "SVG", "value": "svg"},
                                ],
                                value="svg",
                                inline=True,
                                style={
                                    "fontSize": "12px",
                                    "display": "inline-block",
                                    "marginRight": "10px",
                                },
                            ),
                            html.Button(
                                "⬇️ 导出图片",
                                id="export-image-btn",
                                n_clicks=0,
                                style={
                                    "padding": "4px 12px",
                                    "backgroundColor": "#1a73e8",
                                    "color": "white",
                                    "border": "none",
                                    "borderRadius": "4px",
                                    "cursor": "pointer",
                                    "fontSize": "12px",
                                    "marginRight": "20px",
                                },
                            ),
                        ],
                        style={
                            "display": "inline-block",
                            "marginRight": "15px",
                            "marginBottom": "5px",
                        },
                    ),
                    # 数据导出组
                    html.Div(
                        [
                            html.Span(
                                "📤 导出数据：",
                                style={
                                    "fontSize": "12px",
                                    "fontWeight": "bold",
                                    "marginRight": "6px",
                                    "whiteSpace": "nowrap",
                                },
                            ),
                            dcc.RadioItems(
                                id="data-format",
                                options=[
                                    {"label": "JSON", "value": "json"},
                                    {"label": "DOT", "value": "dot"},
                                    {"label": "HTML", "value": "html"},
                                ],
                                value="json",
                                inline=True,
                                style={
                                    "fontSize": "12px",
                                    "display": "inline-block",
                                    "marginRight": "10px",
                                },
                            ),
                            html.Button(
                                "⬇️ 导出数据",
                                id="export-data-btn",
                                n_clicks=0,
                                style={
                                    "padding": "4px 12px",
                                    "backgroundColor": "#10b981",
                                    "color": "white",
                                    "border": "none",
                                    "borderRadius": "4px",
                                    "cursor": "pointer",
                                    "fontSize": "12px",
                                },
                            ),
                        ],
                        style={"display": "inline-block", "marginBottom": "5px"},
                    ),
                ],
                style={
                    "textAlign": "right",
                    "backgroundColor": "white",
                    "padding": "8px 15px",
                    "borderRadius": "8px",
                    "boxShadow": "0 2px 8px rgba(0,0,0,0.1)",
                    "marginBottom": "15px",
                    "flexWrap": "wrap",
                },
            ),
            # ═══════════════════════════════════════════════════════════
            # ③ 页头区
            # ═══════════════════════════════════════════════════════════
            html.H1(
                "🧠 通用任务规划助手",
                style={
                    "textAlign": "center",
                    "marginBottom": "5px",
                    "color": "#1e293b",
                },
            ),
            html.P(
                "输入任务需求，AI 自动分解为 DAG 流程图，逐步确认执行",
                style={
                    "textAlign": "center",
                    "color": "#64748b",
                    "marginBottom": "10px",
                },
            ),
            html.Div(
                id="global-status",
                children="👋 就绪，等待操作",
                style={
                    "marginBottom": "8px",
                    "fontWeight": "bold",
                    "fontSize": "14px",
                    "color": "#1e293b",
                    "textAlign": "center",
                },
            ),
            # ═══════════════════════════════════════════════════════════
            # ④ 全局进度条
            # ═══════════════════════════════════════════════════════════
            html.Div(
                [
                    html.Div(
                        id="progress-bar-fill",
                        style={
                            "width": "0%",
                            "height": "100%",
                            "background": "linear-gradient(90deg, #10b981, #34d399)",
                            "borderRadius": "6px",
                            "transition": "width 0.5s ease",
                        },
                    ),
                ],
                style={
                    "width": "60%",
                    "height": "8px",
                    "backgroundColor": "#e2e8f0",
                    "borderRadius": "6px",
                    "margin": "0 auto 15px auto",
                    "overflow": "hidden",
                },
            ),
            # ═══════════════════════════════════════════════════════════
            # ⑤ 主标签页
            # ═══════════════════════════════════════════════════════════
            dcc.Tabs(
                id="main-tabs",
                value="tab-new",
                children=[
                    # ──────── Tab 1: 新建任务 ────────
                    dcc.Tab(
                        label="🚀 新建任务",
                        value="tab-new",
                        children=[
                            html.Div(
                                [
                                    # 输入区
                                    html.Div(
                                        [
                                            dcc.Textarea(
                                                id="user-input",
                                                placeholder="输入任务需求，例如：教我做甜口西红柿炒鸡蛋（最多 2000 字符）",
                                                style={
                                                    "width": "100%",
                                                    "height": "90px",
                                                    "fontSize": "14px",
                                                    "padding": "10px",
                                                    "borderRadius": "8px",
                                                    "border": "1px solid #d1d5db",
                                                },
                                            ),
                                            dcc.Checklist(
                                                id="enable-refine",
                                                options=[
                                                    {"label": "🔧 节点细化", "value": "refine"}
                                                ],
                                                value=["refine"],
                                                style={"marginTop": "10px"},
                                            ),
                                        ],
                                        style={"marginBottom": "15px"},
                                    ),
                                    # 任务控制区
                                    html.Div(
                                        [
                                            html.Div("📋 任务控制", style=SECTION_HEADER_STYLE),
                                            html.Div(
                                                [
                                                    html.Button(
                                                        "🚀 开始规划",
                                                        id="submit-btn",
                                                        n_clicks=0,
                                                        style=BUTTON_STYLE_PRIMARY,
                                                    ),
                                                    html.Button(
                                                        "⏹️ 停止",
                                                        id="stop-btn",
                                                        n_clicks=0,
                                                        disabled=True,
                                                        style=BUTTON_STYLE_DANGER,
                                                    ),
                                                    html.Button(
                                                        "▶️ 继续",
                                                        id="resume-btn",
                                                        n_clicks=0,
                                                        disabled=True,
                                                        style=BUTTON_STYLE_SECONDARY,
                                                    ),
                                                ]
                                            ),
                                        ],
                                        style={
                                            "padding": "14px 18px",
                                            "backgroundColor": "#eff6ff",
                                            "border": "1px solid #bfdbfe",
                                            "borderRadius": "10px",
                                            "marginBottom": "12px",
                                        },
                                    ),
                                    # 节点操作区
                                    html.Div(
                                        [
                                            html.Div(
                                                "🎯 节点操作（先点击流程图中的节点，再选择操作）",
                                                style=SECTION_HEADER_STYLE,
                                            ),
                                            html.Div(
                                                [
                                                    html.Button(
                                                        "▶️ 开始执行选中节点",
                                                        id="start-exec-btn",
                                                        n_clicks=0,
                                                        disabled=True,
                                                        style={
                                                            "backgroundColor": "#10b981",
                                                            "color": "white",
                                                            "border": "none",
                                                            "padding": "12px 20px",
                                                            "borderRadius": "8px",
                                                            "cursor": "pointer",
                                                            "fontSize": "14px",
                                                            "fontWeight": "bold",
                                                            "marginRight": "10px",
                                                        },
                                                    ),
                                                    html.Button("🔄 重试选中节点", id="start-exec-btn", n_clicks=0, disabled=True, style={
                                                                    "backgroundColor": "#f59e0b", # 橙色代表重试
                                                                    "color": "white",
                                                                    "border": "none", "padding": "12px 20px",
                                                                    "borderRadius": "8px", "cursor": "pointer",
                                                                    "fontSize": "14px", "fontWeight": "bold",
                                                                    "marginRight": "10px",
                                                                }),
                                                    html.Button(
                                                        "⏭️ 跳过选中节点",
                                                        id="skip-btn",
                                                        n_clicks=0,
                                                        disabled=True,
                                                        style={
                                                            "backgroundColor": "#6366f1",
                                                            "color": "white",
                                                            "border": "none",
                                                            "padding": "12px 20px",
                                                            "borderRadius": "8px",
                                                            "cursor": "pointer",
                                                            "fontSize": "14px",
                                                            "fontWeight": "bold",
                                                            "marginRight": "10px",
                                                        },
                                                    ),
                                                    html.Button(
                                                        "❌ 做不到",
                                                        id="fail-btn",
                                                        n_clicks=0,
                                                        disabled=True,
                                                        style={
                                                            "backgroundColor": "#ef4444",
                                                            "color": "white",
                                                            "border": "none",
                                                            "padding": "12px 20px",
                                                            "borderRadius": "8px",
                                                            "cursor": "pointer",
                                                            "fontSize": "14px",
                                                            "fontWeight": "bold",
                                                        },
                                                    ),
                                                ]
                                            ),
                                            html.Div(
                                                id="node-action-hint",
                                                children="👆 请先在流程图中点击一个高亮（蓝色）节点",
                                                style={
                                                    "fontSize": "13px",
                                                    "color": "#94a3b8",
                                                    "marginTop": "8px",
                                                    "fontStyle": "italic",
                                                },
                                            ),
                                        ],
                                        style={
                                            "padding": "14px 18px",
                                            "backgroundColor": "#f0fdf4",
                                            "border": "1px solid #bbf7d0",
                                            "borderRadius": "10px",
                                            "marginBottom": "15px",
                                        },
                                    ),
                                    # 缩放工具栏
                                    html.Div(
                                        [
                                            html.Button(
                                                "⊡ 适应窗口",
                                                id="fit-btn",
                                                n_clicks=0,
                                                style={**ZOOM_TOOLBAR_STYLE, "marginRight": "8px"},
                                            ),
                                            html.Button(
                                                "＋ 放大",
                                                id="zoom-in-btn",
                                                n_clicks=0,
                                                style={**ZOOM_TOOLBAR_STYLE, "marginRight": "4px"},
                                            ),
                                            html.Button(
                                                "－ 缩小",
                                                id="zoom-out-btn",
                                                n_clicks=0,
                                                style={**ZOOM_TOOLBAR_STYLE, "marginRight": "8px"},
                                            ),
                                            html.Div(
                                                dcc.Slider(
                                                    id="zoom-slider",
                                                    min=0.2,
                                                    max=4.0,
                                                    step=0.1,
                                                    value=0.6,
                                                    marks={
                                                        0.2: "20%",
                                                        0.6: "60%",
                                                        1: "100%",
                                                        2: "200%",
                                                        4: "400%",
                                                    },
                                                    tooltip={
                                                        "placement": "bottom",
                                                        "always_visible": True,
                                                    },
                                                ),
                                                style={
                                                    "width": "200px",
                                                    "display": "inline-block",
                                                    "verticalAlign": "middle",
                                                },
                                            ),
                                        ],
                                        style={"marginBottom": "10px"},
                                    ),
                                    # 流程图
                                    cyto.Cytoscape(
                                        id="flowchart",
                                        elements=[],
                                        layout={
                                            "name": "dagre",
                                            "rankDir": "TB",
                                            "nodeSep": 300,
                                            "rankSep": 450,
                                            "edgeSep": 80,
                                            "fit": True,
                                            "padding": 80,
                                        },
                                        style={
                                            "width": "100%",
                                            "height": "700px",
                                            "border": "1px solid #e2e8f0",
                                            "borderRadius": "12px",
                                        },
                                        stylesheet=CYTO_STYLESHEET,
                                        userZoomingEnabled=True,
                                        userPanningEnabled=True,
                                        boxSelectionEnabled=False,
                                        autoungrabify=False,
                                        minZoom=0.2,
                                        maxZoom=4.0,
                                        zoom=0.6,
                                    ),
                                    # 节点详情面板
                                    html.Div(
                                        id="node-detail-panel",
                                        children=[
                                            dcc.Markdown(
                                                "👆 **点击流程图中的节点查看详情并操作**",
                                                style={"fontSize": "14px", "color": "#94a3b8"},
                                            )
                                        ],
                                        style={
                                            "marginTop": "15px",
                                            "padding": "18px",
                                            "border": "1px solid #e2e8f0",
                                            "borderRadius": "12px",
                                            "backgroundColor": "#f8fafc",
                                            "minHeight": "160px",
                                            "fontSize": "14px",
                                            "lineHeight": "1.8",
                                        },
                                    ),
                                ],
                                style={"padding": "25px", "maxWidth": "1400px", "margin": "0 auto"},
                            ),
                        ],
                    ),
                    # ──────── Tab 2: 历史记录 ────────
                    dcc.Tab(
                        label="📜 历史记录",
                        value="tab-history",
                        children=[
                            html.Div(
                                [
                                    # 左栏：任务列表
                                    html.Div(
                                        [
                                            html.H3("📋 任务列表", style={"marginTop": "0"}),
                                            html.Button(
                                                "🔄 刷新列表",
                                                id="refresh-btn",
                                                n_clicks=0,
                                                style={
                                                    "width": "100%",
                                                    "padding": "10px",
                                                    "backgroundColor": "#f1f5f9",
                                                    "border": "1px solid #d1d5db",
                                                    "borderRadius": "6px",
                                                    "cursor": "pointer",
                                                    "marginBottom": "12px",
                                                },
                                            ),
                                            dcc.Dropdown(
                                                id="history-dropdown",
                                                options=[],
                                                placeholder="选择历史任务（可多选删除）",
                                                multi=True,
                                                style={"marginBottom": "12px"},
                                            ),
                                            html.Button(
                                                "🗑️ 删除选中",
                                                id="delete-btn",
                                                n_clicks=0,
                                                style={
                                                    "width": "100%",
                                                    "padding": "10px",
                                                    "backgroundColor": "#ef4444",
                                                    "color": "white",
                                                    "border": "none",
                                                    "borderRadius": "6px",
                                                    "cursor": "pointer",
                                                    "marginBottom": "10px",
                                                },
                                            ),
                                            html.Div(
                                                id="delete-status",
                                                style={"fontSize": "13px", "color": "#059669"},
                                            ),
                                        ],
                                        style={
                                            "width": "28%",
                                            "display": "inline-block",
                                            "verticalAlign": "top",
                                            "padding": "20px",
                                            "borderRight": "1px solid #e2e8f0",
                                        },
                                    ),
                                    # 右栏：控制区 + 流程图 + 详情
                                    html.Div(
                                        [
                                            # 历史任务控制区
                                            html.Div(
                                                [
                                                    html.Div(
                                                        "🎮 任务控制（基于历史任务）",
                                                        style=SECTION_HEADER_STYLE,
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Button(
                                                                "🔄 重新规划",
                                                                id="history-replan-btn",
                                                                n_clicks=0,
                                                                disabled=True,
                                                                style={
                                                                    "backgroundColor": "#1a73e8",
                                                                    "color": "white",
                                                                    "border": "none",
                                                                    "padding": "10px 18px",
                                                                    "borderRadius": "6px",
                                                                    "cursor": "pointer",
                                                                    "fontSize": "13px",
                                                                    "fontWeight": "bold",
                                                                    "marginRight": "8px",
                                                                },
                                                            ),
                                                            html.Button(
                                                                "✏️ 修改规划",
                                                                id="history-edit-plan-btn",
                                                                n_clicks=0,
                                                                disabled=True,
                                                                style={
                                                                    "backgroundColor": "#8b5cf6",
                                                                    "color": "white",
                                                                    "border": "none",
                                                                    "padding": "10px 18px",
                                                                    "borderRadius": "6px",
                                                                    "cursor": "pointer",
                                                                    "fontSize": "13px",
                                                                    "fontWeight": "bold",
                                                                    "marginRight": "8px",
                                                                },
                                                            ),
                                                            html.Button(
                                                                "▶️ 继续执行",
                                                                id="history-resume-btn",
                                                                n_clicks=0,
                                                                disabled=True,
                                                                style={
                                                                    "backgroundColor": "#10b981",
                                                                    "color": "white",
                                                                    "border": "none",
                                                                    "padding": "10px 18px",
                                                                    "borderRadius": "6px",
                                                                    "cursor": "pointer",
                                                                    "fontSize": "13px",
                                                                    "fontWeight": "bold",
                                                                    "marginRight": "8px",
                                                                },
                                                            ),
                                                            html.Button(
                                                                "🔁 重试失败节点",
                                                                id="history-retry-btn",
                                                                n_clicks=0,
                                                                disabled=True,
                                                                style={
                                                                    "backgroundColor": "#f59e0b",
                                                                    "color": "white",
                                                                    "border": "none",
                                                                    "padding": "10px 18px",
                                                                    "borderRadius": "6px",
                                                                    "cursor": "pointer",
                                                                    "fontSize": "13px",
                                                                    "fontWeight": "bold",
                                                                },
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        id="history-action-status",
                                                        children="👆 请先从左侧列表选择一个历史任务",
                                                        style={
                                                            "fontSize": "12px",
                                                            "color": "#94a3b8",
                                                            "marginTop": "8px",
                                                            "fontStyle": "italic",
                                                        },
                                                    ),
                                                ],
                                                style={
                                                    "padding": "14px 18px",
                                                    "backgroundColor": "#eff6ff",
                                                    "border": "1px solid #bfdbfe",
                                                    "borderRadius": "10px",
                                                    "marginBottom": "15px",
                                                },
                                            ),
                                            # 历史节点操作区
                                            html.Div(
                                                [
                                                    html.Div(
                                                        "🎯 节点操作（先点击历史流程图中的节点）",
                                                        style=SECTION_HEADER_STYLE,
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Button(
                                                                "标记完成",
                                                                id="history-node-done-btn",
                                                                n_clicks=0,
                                                                disabled=True,
                                                                style={
                                                                    "backgroundColor": "#10b981",
                                                                    "color": "white",
                                                                    "border": "none",
                                                                    "padding": "10px 18px",
                                                                    "borderRadius": "6px",
                                                                    "cursor": "pointer",
                                                                    "fontSize": "13px",
                                                                    "fontWeight": "bold",
                                                                    "marginRight": "8px",
                                                                },
                                                            ),
                                                            html.Button(
                                                                "⏭️ 跳过",
                                                                id="history-node-skip-btn",
                                                                n_clicks=0,
                                                                disabled=True,
                                                                style={
                                                                    "backgroundColor": "#6366f1",
                                                                    "color": "white",
                                                                    "border": "none",
                                                                    "padding": "10px 18px",
                                                                    "borderRadius": "6px",
                                                                    "cursor": "pointer",
                                                                    "fontSize": "13px",
                                                                    "fontWeight": "bold",
                                                                    "marginRight": "8px",
                                                                },
                                                            ),
                                                            html.Button(
                                                                "❌ 标记失败",
                                                                id="history-node-fail-btn",
                                                                n_clicks=0,
                                                                disabled=True,
                                                                style={
                                                                    "backgroundColor": "#ef4444",
                                                                    "color": "white",
                                                                    "border": "none",
                                                                    "padding": "10px 18px",
                                                                    "borderRadius": "6px",
                                                                    "cursor": "pointer",
                                                                    "fontSize": "13px",
                                                                    "fontWeight": "bold",
                                                                },
                                                            ),
                                                        ]
                                                    ),
                                                    dcc.Store(
                                                        id="history-selected-node-store", data=""
                                                    ),
                                                    dcc.Store(
                                                        id="history-dag-store",
                                                        data={"nodes": [], "edges": []},
                                                    ),
                                                    dcc.Store(id="history-node-states-store"),
                                                ]
                                            ),
                                            # 历史流程图
                                            html.Div(
                                                [
                                                    html.H3("🗺️ 流程图", style={"marginTop": "0"}),
                                                    html.Div(
                                                        [
                                                            html.Button(
                                                                "⊡ 适应窗口",
                                                                id="history-fit-btn",
                                                                n_clicks=0,
                                                                style={
                                                                    **ZOOM_TOOLBAR_STYLE,
                                                                    "marginRight": "8px",
                                                                },
                                                            ),
                                                            html.Button(
                                                                "＋ 放大",
                                                                id="history-zoom-in-btn",
                                                                n_clicks=0,
                                                                style={
                                                                    **ZOOM_TOOLBAR_STYLE,
                                                                    "marginRight": "4px",
                                                                },
                                                            ),
                                                            html.Button(
                                                                "－ 缩小",
                                                                id="history-zoom-out-btn",
                                                                n_clicks=0,
                                                                style={
                                                                    **ZOOM_TOOLBAR_STYLE,
                                                                    "marginRight": "8px",
                                                                },
                                                            ),
                                                            html.Div(
                                                                dcc.Slider(
                                                                    id="history-zoom-slider",
                                                                    min=0.2,
                                                                    max=4.0,
                                                                    step=0.1,
                                                                    value=0.6,
                                                                    marks={
                                                                        0.2: "20%",
                                                                        0.6: "60%",
                                                                        1: "100%",
                                                                        2: "200%",
                                                                        4: "400%",
                                                                    },
                                                                    tooltip={
                                                                        "placement": "bottom",
                                                                        "always_visible": True,
                                                                    },
                                                                ),
                                                                style={
                                                                    "width": "200px",
                                                                    "display": "inline-block",
                                                                    "verticalAlign": "middle",
                                                                },
                                                            ),
                                                        ],
                                                        style={"marginBottom": "10px"},
                                                    ),
                                                    cyto.Cytoscape(
                                                        id="history-flowchart",
                                                        elements=[],
                                                        layout={
                                                            "name": "dagre",
                                                            "rankDir": "TB",
                                                            "nodeSep": 250,
                                                            "rankSep": 380,
                                                            "edgeSep": 60,
                                                            "fit": True,
                                                            "padding": 60,
                                                        },
                                                        style={
                                                            "width": "100%",
                                                            "height": "450px",
                                                            "border": "1px solid #e2e8f0",
                                                            "borderRadius": "12px",
                                                            "marginBottom": "15px",
                                                        },
                                                        stylesheet=CYTO_STYLESHEET,
                                                        minZoom=0.2,
                                                        maxZoom=4.0,
                                                        zoom=0.6,
                                                        userZoomingEnabled=True,
                                                        userPanningEnabled=True,
                                                    ),
                                                    # 节点详情
                                                    html.Div(
                                                        [
                                                            html.H4(
                                                                "🎯 节点详情",
                                                                style={
                                                                    "margin": "0 0 8px",
                                                                    "fontSize": "14px",
                                                                },
                                                            ),
                                                            html.Div(
                                                                id="history-node-detail",
                                                                children=[
                                                                    dcc.Markdown(
                                                                        "**点击流程图中的节点查看详情**"
                                                                    )
                                                                ],
                                                                style={
                                                                    "padding": "10px",
                                                                    "border": "1px solid #e2e8f0",
                                                                    "borderRadius": "8px",
                                                                    "backgroundColor": "#f8fafc",
                                                                    "minHeight": "80px",
                                                                    "fontSize": "13px",
                                                                    "marginBottom": "10px",
                                                                },
                                                            ),
                                                        ],
                                                        style={"marginBottom": "15px"},
                                                    ),
                                                    # 任务详情
                                                    html.H3("📝 任务详情"),
                                                    html.Div(
                                                        id="history-detail",
                                                        style={
                                                            "whiteSpace": "pre-wrap",
                                                            "padding": "15px",
                                                            "border": "1px solid #e2e8f0",
                                                            "borderRadius": "8px",
                                                            "backgroundColor": "#f8fafc",
                                                            "fontSize": "14px",
                                                            "lineHeight": "1.8",
                                                        },
                                                    ),
                                                ],
                                                style={
                                                    "width": "68%",
                                                    "display": "inline-block",
                                                    "padding": "20px",
                                                    "verticalAlign": "top",
                                                },
                                            ),
                                        ]
                                    ),
                                ]
                            ),
                        ],
                    ),
                ],
            ),  # ← 关键：关闭 dcc.Tabs，之前这一段缺失导致整个函数语法崩溃
            # ═══════════════════════════════════════════════════════════
            # ⑥ 配置抽屉（悬浮层，覆盖在页面之上）
            # ═══════════════════════════════════════════════════════════
            # 说明：必须在 children 最后——保证浮层渲染在页面之上，
            # 不受 DOM 顺序影响 z-index 竞争（CSS z-index 已经 9998/9999，
            # 但放进最后可以杜绝未来子组件引入更高 z-index 时的冲突）
            _build_config_trigger(),
            _build_config_drawer(),
        ]
    )
