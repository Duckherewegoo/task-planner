"""
dash_app.py — 完整 Dash UI + 交互式任务执行（异步回调版）
包含：新建任务流式执行 + 历史记录 + 节点交互确认 + 导出 + 图操作优化 + 循环边支持
图片导出方案：PyVis 后端渲染 -> pydot (生成 DOT/SVG) → cairosvg (SVG 转 PNG) → io.BytesIO (内存缓冲) -> dcc.sendbytes 直接传输字节流 -> PNG 下载
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Tuple
from dataclasses import dataclass, fields
import hashlib
import dash
from dash import dcc, html, Input, Output, State, callback, clientside_callback, no_update, ctx
import dash_cytoscape as cyto

from task_planner.utils.cytoscape_adapter import _get_edge_type
from task_planner.utils.pyvis_export import export_dag_to_svg, export_dag_to_png
from task_planner.services.agent import run_task_stream
from task_planner.utils.good_addons import boost
from task_planner.utils.cytoscape_adapter import dag_to_cytoscape, build_detail_markdown, build_history_detail_markdown
from task_planner.core.graph.nodes import sanitize_node_for_user
from task_planner.services.stream_manager import (
    start_stream,
    get_stream_state,
    cancel_stream,
    complete_node,
    skip_node,
    fail_node,
    get_ready_nodes,
)
from task_planner.core.database import (
    init_db,
    list_tasks,
    load_task_with_plan,
    batch_delete_tasks,
    reset_node_status,
)
from task_planner.utils.presentation_utils import build_history_dropdown_options, extract_first_task_id
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import (
    EDGE_TYPE_HARD,
    NODE_ACTION_CONFIG,
    NODE_OPERABLE_STATES,
    NODE_STATUS_CODE_MAP,
    STATE_COLORS,
    STATE_LABELS,
    VALID_PORT_RANGE,
)
from task_planner.infrastructure.ui_styles import (
    BAR_DONE,
    BAR_ERROR,
    BAR_LOADING,
    BASE_BTN_STYLE,
    BUTTON_STYLE_DANGER,
    BUTTON_STYLE_PRIMARY,
    BUTTON_STYLE_SECONDARY,
    EMPTY_BAR_MINI_STYLE,
    EMPTY_BAR_STYLE,
    MARKDOWN_PRE_STYLE,
    SECTION_HEADER_STYLE,
    ZOOM_TOOLBAR_STYLE,
)
from task_planner.infrastructure.assets.cytoscape_styles import CYTO_STYLESHEET
from task_planner.infrastructure.assets.cytoscape_js import (
    FIT_JS_TEMPLATE,
    GRAPH_CONFIGS,
    ZOOM_BTN_JS_TEMPLATE,
    ZOOM_SLIDER_JS_TEMPLATE,
)
DASH_DISABLE_VERSION_CHECK = _hub.dev.DASH_DISABLE_VERSION_CHECK
DASH_HOST = _hub.dev.DASH_HOST
DASH_PORT = _hub.dev.DASH_PORT
DEBUG = _hub.dev.DEBUG
MAX_CACHE_INPUT_BYTES = _hub.dev.MAX_CACHE_INPUT_BYTES
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.dash_app")

cyto.load_extra_layouts()


# ✅ P0-1 修复：init_db 是 async，必须用 asyncio.run 或延迟到 main() 里执行。
#    这里用 asyncio.run 保证模块导入后 DB 一定初始化；
#    失败时不静默吞掉，而是明确告警（DB 是强依赖，不初始化后续必崩）。
def _bootstrap_db() -> None:
    """模块导入时执行一次 DB 初始化。"""
    from task_planner.core.database import init_db
    try:
        asyncio.run(init_db())
        logger.info("[Dash] ✅ 数据库初始化完成")
    except Exception as e:
        logger.warning("[Dash] ⚠️ 数据库初始化失败，将在首次请求时重试: %s", e)


_bootstrap_db()

# ══════════════════════════════════════════════════
#  历史回调的结果容器（✅ P0-5 修复：原代码引用了未定义的类）
# ══════════════════════════════════════════════════

@dataclass
class HistorySelectResult:
    """
    on_history_select 回调的返回结果聚合。
    通过 to_tuple() 展开为 Dash 多输出元组。

    输出顺序必须与 @callback 装饰器声明的 Output 顺序完全一致：
      1. history-flowchart.elements
      2. history-detail.children
      3. history-flowchart.pan
      4. global-status.children
      5. dag-store.data
      6. history-replan-btn.disabled
      7. history-edit-plan-btn.disabled
      8. history-resume-btn.disabled
      9. history-retry-btn.disabled
      10. history-action-status.children
      11. history-dag-store.data
      12. history-node-states-store.data
    """
    elements: Any = None
    detail: Any = None
    pan: Any = None
    global_status: Any = None
    dag_store: Any = None
    replan_disabled: Any = True
    edit_disabled: Any = True
    resume_disabled: Any = True
    retry_disabled: Any = True
    action_status: Any = None
    history_dag_store: Any = None
    history_node_states: Any = None

    def to_tuple(self) -> tuple:
        return (
            self.elements if self.elements is not None else no_update,
            self.detail if self.detail is not None else no_update,
            self.pan if self.pan is not None else no_update,
            self.global_status if self.global_status is not None else no_update,
            self.dag_store if self.dag_store is not None else no_update,
            self.replan_disabled,
            self.edit_disabled,
            self.resume_disabled,
            self.retry_disabled,
            self.action_status if self.action_status is not None else no_update,
            self.history_dag_store if self.history_dag_store is not None else no_update,
            self.history_node_states if self.history_node_states is not None else no_update,
        )


@dataclass
class HistoryTapNodeResult:
    """
    on_history_tap_node 回调的返回结果聚合。

    输出顺序（与装饰器一致）：
      1. history-node-detail.children
      2. history-selected-node-store.data
      3. history-node-done-btn.disabled
      4. history-node-skip-btn.disabled
      5. history-node-fail-btn.disabled
    """
    detail: Any = None
    selected_node: Any = None
    done_disabled: bool = True
    skip_disabled: bool = True
    fail_disabled: bool = True

    def to_tuple(self) -> tuple:
        return (
            self.detail if self.detail is not None else no_update,
            self.selected_node if self.selected_node is not None else no_update,
            self.done_disabled,
            self.skip_disabled,
            self.fail_disabled,
        )

app = dash.Dash(
    __name__,
    title="🧠 通用任务规划助手",
    assets_folder="assets",
    suppress_callback_exceptions=True,
)

os.environ["DASH_DISABLE_VERSION_CHECK"] = str(DASH_DISABLE_VERSION_CHECK)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TOOLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _resolve_dag_data(
    history_task_ids,
    dag_store,
    node_states_store=None,
):
    """
    统一的数据源解析引擎（双源适配器）。
    优先级契约：历史持久化数据 > 当前内存 Store。

    ✅ P0-2 修复：改为 async def，内部 await load_task_with_plan。

    Returns:
        tuple: (nodes, edges, node_states, task_id_str)
        若数据为空则返回 (None, None, None, None)
    """
    from task_planner.core.database import load_task_with_plan

    task_id = None
    if history_task_ids:
        task_id = (
            history_task_ids[0]
            if isinstance(history_task_ids, list)
            else history_task_ids
        )

    if task_id:
        try:
            # ✅ 正确 await
            data = await load_task_with_plan(task_id)
        except Exception as e:
            logger.error(
                "[Export] 加载历史任务失败 task=%s: %s",
                task_id, e, exc_info=True,
            )
            data = None

        if data:
            plan = data.get("plan") or {}
            nodes = plan.get("nodes") or []
            edges = plan.get("edges") or []
            node_states = data.get("node_states") or node_states_store
            task_id_str = str(task_id)[-8:]
            return nodes, edges, node_states, task_id_str
        else:
            logger.warning("[Export] 未找到历史任务数据: %s", task_id)
            return None, None, None, None

    # 降级：使用当前内存 Store
    nodes = (dag_store or {}).get("nodes") or []
    edges = (dag_store or {}).get("edges") or []
    return nodes, edges, node_states_store, "current"

def _make_filename(content_type: str, task_id_str: str, ext: str) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{content_type}_{task_id_str}_{timestamp}.{ext}"


def _safe_elapsed(task_start_time, now):
    try:
        start = float(task_start_time)
        if start <= 0:
            return 0.0
        return round(now - start, 1)
    except (TypeError, ValueError):
        return 0.0


def update_node_and_render(dag: dict, node_states: dict, nid: str, target_status: str) -> tuple:
    """
    统一节点状态更新引擎。
    根据 NODE_ACTION_CONFIG 自动处理下游解锁、渲染和统计。
    """
    config = NODE_ACTION_CONFIG.get(target_status)
    if not config:
        raise ValueError(f"未知的节点操作类型: {target_status}")

    new_states = dict(node_states)
    new_states[nid] = target_status

    # 根据配置决定是否解锁下游
    if config["unlock_downstream"]:
        new_states = _auto_unlock_downstream(dag, new_states, nid, target_status)

    # 重新渲染
    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    elements = dag_to_cytoscape(nodes, edges, new_states)

    # 多维统计
    total = len(nodes)
    counts = {s: sum(1 for v in new_states.values() if v == s) for s in ("done", "skipped", "failed")}
    processed = counts["done"] + counts["skipped"]

    status_msg = (
        f"{config['icon']} 节点 {nid} 已标记{config['label']} | "
        f"进度: {processed}/{total} "
        f"(完成:{counts['done']} 跳过:{counts['skipped']} 失败:{counts['failed']})"
    )

    return new_states, elements, status_msg


async def _load_and_fill_query(task_ids, action_label: str) -> tuple:
    """
    统一的历史任务回填引擎。

    ✅ P0-3 修复：改为 async def，内部 await load_task_with_plan。
    """
    from task_planner.core.database import load_task_with_plan

    task_id = extract_first_task_id(task_ids)

    if not task_id:
        return "⚠️ 请先选择历史任务", no_update, no_update

    try:
        # ✅ 正确 await
        data = await load_task_with_plan(task_id)
    except Exception as e:
        logger.error(
            "[HistoryAction] %s 加载失败 task=%s: %s",
            action_label, task_id, e, exc_info=True,
        )
        return "❌ 任务数据加载失败，请稍后重试", no_update, no_update

    if not data:
        return "❌ 任务不存在或已被删除", no_update, no_update

    raw_query = (data.get("task") or {}).get("raw_query", "").strip()
    if not raw_query:
        return "⚠️ 该任务无原始需求文本，无法操作", no_update, no_update

    if action_label == "继续执行":
        msg = f"▶️ 已回填上次需求（{len(raw_query)}字），可直接继续或修改后重新规划"
    else:
        msg = f"🔄 [{action_label}] 已回填原始需求（{len(raw_query)}字），请确认后点击「开始规划」"

    logger.info(
        "[HistoryAction] %s 回填成功 task=%s query_len=%d",
        action_label, task_id, len(raw_query),
    )
    return msg, "tab-new", raw_query

def _auto_unlock_downstream(dag, node_states, changed_nid, new_state, allowed_edge_types=None):
    """
    通用下游建议性解锁引擎。
    """
    edges = dag.get("edges", [])
    new_states = dict(node_states or {})

    if allowed_edge_types is not None:
        check_edge_types = set(allowed_edge_types)
    elif new_state == "done":
        check_edge_types = {EDGE_TYPE_HARD}
    else:
        check_edge_types = {EDGE_TYPE_HARD}

    downstream_edges_map = {}
    for e in edges:
        src, tgt = str(e.get("from", "")), str(e.get("to", ""))
        if src == changed_nid and _get_edge_type(e) in check_edge_types:
            downstream_edges_map.setdefault(tgt, []).append(e)

    for tgt, relevant_in_edges in downstream_edges_map.items():
        current_state = new_states.get(tgt, "pending")
        if current_state not in ("pending", "blocked"):
            continue
        all_satisfied = all(
            new_states.get(str(e["from"])) == "done"
            for e in relevant_in_edges
        )
        if all_satisfied:
            new_states[tgt] = "running"

    return new_states


def _generate_html_export(nodes, edges, task_id):
    import textwrap
    elements_list = []
    for i, n in enumerate(nodes):
        nid = str(n.get("id", i))
        label = n.get("name", n.get("node_name", f"Node {i}"))
        detail = n.get("detail", n.get("details", ""))
        elements_list.append({"data": {"id": nid, "label": label, "detail": detail}})

    for i, e in enumerate(edges):
        elements_list.append({
            "data": {
                "id": f"e{i}",
                "source": str(e["from"]),
                "target": str(e["to"]),
                "label": e.get("label", ""),
            }
        })

    safe_json = json.dumps(elements_list, ensure_ascii=False).replace("</", r"<\/")

    return textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>流程图 {task_id}</title>
            <script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
            <script src="https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
            <style>
                body {{ margin: 0; font-family: Arial; }}
                #cy {{ width: 100%; height: 100vh; }}
            </style>
        </head>
        <body>
            <div id="cy"></div>
            <script>
                var cy = cytoscape({{
                    container: document.getElementById('cy'),
                    elements: {safe_json},
                    layout: {{ name: 'dagre', rankDir: 'TB', nodeSep: 300, rankSep: 450 }},
                    style: [
                        {{
                            selector: 'node',
                            style: {{
                                'label': 'data(label)',
                                'text-valign': 'center',
                                'text-halign': 'center',
                                'font-size': 13,
                                'shape': 'round-rectangle',
                                'padding': 12,
                                'background-color': '#dbeafe',
                                'border-color': '#3b82f6',
                                'border-width': 2
                            }}
                        }},
                        {{
                            selector: 'edge',
                            style: {{
                                'curve-style': 'bezier',
                                'target-arrow-shape': 'triangle',
                                'label': 'data(label)',
                                'font-size': 11
                            }}
                        }}
                    ]
                }});
            </script>
        </body>
        </html>
    """)


# ── 布局 ──
def build_layout() -> html.Div:
    return html.Div([ 
        dcc.Interval(id="stream-interval", interval=500, disabled=True, n_intervals=0),
        dcc.Store(id="thread-id-store", data=""),
        dcc.Store(id="stream-active", data=False),
        dcc.Store(id="node-states-store", data={}),
        dcc.Store(id="dag-store", data={"nodes": [], "edges": []}),
        dcc.Store(id="selected-node-store", data=""),
        dcc.Store(id="task-start-time", data=None),
        dcc.Store(id="history-replan-trigger", data=0),
        dcc.Download(id="export-download"),
        html.Div([
            html.Div([
                html.Span("📤 导出图像：", style={
                    "fontSize": "12px", "fontWeight": "bold",
                    "marginRight": "6px", "whiteSpace": "nowrap",
                }),
                dcc.RadioItems(
                    id="image-format",
                    options=[
                        {"label": "PNG", "value": "png"},
                        {"label": "SVG", "value": "svg"},
                    ],
                    value="svg",
                    inline=True,
                    style={"fontSize": "12px", "display": "inline-block", "marginRight": "10px"},
                ),
                html.Button("⬇️ 导出图片", id="export-image-btn", n_clicks=0, style={
                    "padding": "4px 12px", "backgroundColor": "#1a73e8", "color": "white",
                    "border": "none", "borderRadius": "4px", "cursor": "pointer",
                    "fontSize": "12px", "marginRight": "20px",
                }),
            ], style={"display": "inline-block", "marginRight": "15px", "marginBottom": "5px"}),
            html.Div([
                html.Span("📤 导出数据：", style={
                    "fontSize": "12px", "fontWeight": "bold",
                    "marginRight": "6px", "whiteSpace": "nowrap",
                }),
                dcc.RadioItems(
                    id="data-format",
                    options=[
                        {"label": "JSON", "value": "json"},
                        {"label": "DOT", "value": "dot"},
                        {"label": "HTML", "value": "html"},
                    ],
                    value="json",
                    inline=True,
                    style={"fontSize": "12px", "display": "inline-block", "marginRight": "10px"},
                ),
                html.Button("⬇️ 导出数据", id="export-data-btn", n_clicks=0, style={
                    "padding": "4px 12px", "backgroundColor": "#10b981", "color": "white",
                    "border": "none", "borderRadius": "4px", "cursor": "pointer",
                    "fontSize": "12px",
                }),
            ], style={"display": "inline-block", "marginBottom": "5px"}),
        ], style={
            "textAlign": "right", "backgroundColor": "white",
            "padding": "8px 15px", "borderRadius": "8px",
            "boxShadow": "0 2px 8px rgba(0,0,0,0.1)",
            "marginBottom": "15px", "flexWrap": "wrap",
        }),
        html.H1("🧠 通用任务规划助手", style={
            "textAlign": "center", "marginBottom": "5px", "color": "#1e293b",
        }),
        html.P("输入任务需求，AI 自动分解为 DAG 流程图，逐步确认执行", style={
            "textAlign": "center", "color": "#64748b", "marginBottom": "10px",
        }),
        html.Div(id="global-status", children="👋 就绪，等待操作", style={
            "marginBottom": "8px", "fontWeight": "bold", "fontSize": "14px",
            "color": "#1e293b", "textAlign": "center",
        }),
        html.Div([
            html.Div(id="progress-bar-fill", style={
                "width": "0%", "height": "100%",
                "background": "linear-gradient(90deg, #10b981, #34d399)",
                "borderRadius": "6px", "transition": "width 0.5s ease",
            }),
        ], style={
            "width": "60%", "height": "8px", "backgroundColor": "#e2e8f0",
            "borderRadius": "6px", "margin": "0 auto 15px auto", "overflow": "hidden",
        }),
        dcc.Tabs(id="main-tabs", value="tab-new", children=[
            dcc.Tab(label="🚀 新建任务", value="tab-new", children=[
                html.Div([
                    html.Div([
                        dcc.Textarea(
                            id="user-input",
                            placeholder="输入任务需求，例如：教我做甜口西红柿炒鸡蛋（最多 2000 字符）",
                            style={
                                "width": "100%", "height": "90px", "fontSize": "14px",
                                "padding": "10px", "borderRadius": "8px",
                                "border": "1px solid #d1d5db",
                            },
                        ),
                        dcc.Checklist(
                            id="enable-refine",
                            options=[{"label": "🔧 节点细化", "value": "refine"}],
                            value=["refine"],
                            style={"marginTop": "10px"},
                        ),
                    ], style={"marginBottom": "15px"}),
                    html.Div([
                        html.Div("📋 任务控制", style=SECTION_HEADER_STYLE),
                        html.Div([
                            html.Button("🚀 开始规划", id="submit-btn", n_clicks=0,
                                       style=BUTTON_STYLE_PRIMARY),
                            html.Button("⏹️ 停止", id="stop-btn", n_clicks=0, disabled=True,
                                       style=BUTTON_STYLE_DANGER),
                            html.Button("▶️ 继续", id="resume-btn", n_clicks=0, disabled=True,
                                       style=BUTTON_STYLE_SECONDARY),
                        ]),
                    ], style={
                        "padding": "14px 18px", "backgroundColor": "#eff6ff",
                        "border": "1px solid #bfdbfe", "borderRadius": "10px",
                        "marginBottom": "12px",
                    }),
                    html.Div([
                        html.Div("🎯 节点操作（先点击流程图中的节点，再选择操作）",
                                style=SECTION_HEADER_STYLE),
                        html.Div([
                            html.Button("▶️ 开始执行选中节点", id="start-exec-btn",
                                       n_clicks=0, disabled=True, style={
                                "backgroundColor": "#10b981", "color": "white",
                                "border": "none", "padding": "12px 20px",
                                "borderRadius": "8px", "cursor": "pointer",
                                "fontSize": "14px", "fontWeight": "bold",
                                "marginRight": "10px",
                            }),
                            html.Button("⏭️ 跳过选中节点", id="skip-btn",
                                       n_clicks=0, disabled=True, style={
                                "backgroundColor": "#6366f1", "color": "white",
                                "border": "none", "padding": "12px 20px",
                                "borderRadius": "8px", "cursor": "pointer",
                                "fontSize": "14px", "fontWeight": "bold",
                                "marginRight": "10px",
                            }),
                            html.Button("❌ 做不到", id="fail-btn",
                                       n_clicks=0, disabled=True, style={
                                "backgroundColor": "#ef4444", "color": "white",
                                "border": "none", "padding": "12px 20px",
                                "borderRadius": "8px", "cursor": "pointer",
                                "fontSize": "14px", "fontWeight": "bold",
                            }),
                        ]),
                        html.Div(id="node-action-hint",
                                children="👆 请先在流程图中点击一个高亮（蓝色）节点",
                                style={
                                    "fontSize": "13px", "color": "#94a3b8",
                                    "marginTop": "8px", "fontStyle": "italic",
                                }),
                    ], style={
                        "padding": "14px 18px", "backgroundColor": "#f0fdf4",
                        "border": "1px solid #bbf7d0", "borderRadius": "10px",
                        "marginBottom": "15px",
                    }),
                    html.Div([
                        html.Button("⊡ 适应窗口", id="fit-btn", n_clicks=0,
                                   style={**ZOOM_TOOLBAR_STYLE, "marginRight": "8px"}),
                        html.Button("＋ 放大", id="zoom-in-btn", n_clicks=0,
                                   style={**ZOOM_TOOLBAR_STYLE, "marginRight": "4px"}),
                        html.Button("－ 缩小", id="zoom-out-btn", n_clicks=0,
                                   style={**ZOOM_TOOLBAR_STYLE, "marginRight": "8px"}),
                        html.Div(
                            dcc.Slider(
                                id="zoom-slider", min=0.2, max=4.0, step=0.1, value=0.6,
                                marks={0.2: "20%", 0.6: "60%", 1: "100%", 2: "200%", 4: "400%"},
                                tooltip={"placement": "bottom", "always_visible": True},
                            ),
                            style={"width": "200px", "display": "inline-block",
                                   "verticalAlign": "middle"},
                        ),
                    ], style={"marginBottom": "10px"}),
                    cyto.Cytoscape(
                        id="flowchart",
                        elements=[],
                        layout={
                            "name": "dagre", "rankDir": "TB",
                            "nodeSep": 300, "rankSep": 450, "edgeSep": 80,
                            "fit": True, "padding": 80,
                        },
                        style={
                            "width": "100%", "height": "700px",
                            "border": "1px solid #e2e8f0", "borderRadius": "12px",
                        },
                        stylesheet=CYTO_STYLESHEET,
                        userZoomingEnabled=True,
                        userPanningEnabled=True,
                        boxSelectionEnabled=False,
                        autoungrabify=False,
                        minZoom=0.2, maxZoom=4.0, zoom=0.6,
                    ),
                    html.Div(id="node-detail-panel", children=[
                        dcc.Markdown("👆 **点击流程图中的节点查看详情并操作**",
                                    style={"fontSize": "14px", "color": "#94a3b8"})
                    ], style={
                        "marginTop": "15px", "padding": "18px",
                        "border": "1px solid #e2e8f0", "borderRadius": "12px",
                        "backgroundColor": "#f8fafc", "minHeight": "160px",
                        "fontSize": "14px", "lineHeight": "1.8",
                    }),
                ], style={"padding": "25px", "maxWidth": "1400px", "margin": "0 auto"}),
            ]),
            dcc.Tab(label="📜 历史记录", value="tab-history", children=[
                html.Div([
                    html.Div([
                        html.H3("📋 任务列表", style={"marginTop": "0"}),
                        html.Button("🔄 刷新列表", id="refresh-btn", n_clicks=0, style={
                            "width": "100%", "padding": "10px", "backgroundColor": "#f1f5f9",
                            "border": "1px solid #d1d5db", "borderRadius": "6px",
                            "cursor": "pointer", "marginBottom": "12px",
                        }),
                        dcc.Dropdown(
                            id="history-dropdown",
                            options=[],
                            placeholder="选择历史任务（可多选删除）",
                            multi=True,
                            style={"marginBottom": "12px"},
                        ),
                        html.Button("🗑️ 删除选中", id="delete-btn", n_clicks=0, style={
                            "width": "100%", "padding": "10px", "backgroundColor": "#ef4444",
                            "color": "white", "border": "none", "borderRadius": "6px",
                            "cursor": "pointer", "marginBottom": "10px",
                        }),
                        html.Div(id="delete-status", style={"fontSize": "13px", "color": "#059669"}),
                    ], style={
                        "width": "28%", "display": "inline-block", "verticalAlign": "top",
                        "padding": "20px", "borderRight": "1px solid #e2e8f0",
                    }),
                    html.Div([
                        html.Div([
                            html.Div("🎮 任务控制（基于历史任务）", style=SECTION_HEADER_STYLE),
                            html.Div([
                                html.Button("🔄 重新规划", id="history-replan-btn",
                                           n_clicks=0, disabled=True, style={
                                    "backgroundColor": "#1a73e8", "color": "white",
                                    "border": "none", "padding": "10px 18px",
                                    "borderRadius": "6px", "cursor": "pointer",
                                    "fontSize": "13px", "fontWeight": "bold",
                                    "marginRight": "8px",
                                }),
                                html.Button("✏️ 修改规划", id="history-edit-plan-btn",
                                           n_clicks=0, disabled=True, style={
                                    "backgroundColor": "#8b5cf6", "color": "white",
                                    "border": "none", "padding": "10px 18px",
                                    "borderRadius": "6px", "cursor": "pointer",
                                    "fontSize": "13px", "fontWeight": "bold",
                                    "marginRight": "8px",
                                }),
                                html.Button("▶️ 继续执行", id="history-resume-btn",
                                           n_clicks=0, disabled=True, style={
                                    "backgroundColor": "#10b981", "color": "white",
                                    "border": "none", "padding": "10px 18px",
                                    "borderRadius": "6px", "cursor": "pointer",
                                    "fontSize": "13px", "fontWeight": "bold",
                                    "marginRight": "8px",
                                }),
                                html.Button("🔁 重试失败节点", id="history-retry-btn",
                                           n_clicks=0, disabled=True, style={
                                    "backgroundColor": "#f59e0b", "color": "white",
                                    "border": "none", "padding": "10px 18px",
                                    "borderRadius": "6px", "cursor": "pointer",
                                    "fontSize": "13px", "fontWeight": "bold",
                                }),
                            ]),
                            html.Div(id="history-action-status",
                                    children="👆 请先从左侧列表选择一个历史任务",
                                    style={
                                        "fontSize": "12px", "color": "#94a3b8",
                                        "marginTop": "8px", "fontStyle": "italic",
                                    }),
                        ], style={
                            "padding": "14px 18px", "backgroundColor": "#eff6ff",
                            "border": "1px solid #bfdbfe", "borderRadius": "10px",
                            "marginBottom": "15px",
                        }),
                        html.Div([
                            html.Div("🎯 节点操作（先点击历史流程图中的节点）",
                                    style=SECTION_HEADER_STYLE),
                            html.Div([
                                html.Button("✅ 标记完成", id="history-node-done-btn",
                                           n_clicks=0, disabled=True, style={
                                    "backgroundColor": "#10b981", "color": "white",
                                    "border": "none", "padding": "10px 18px",
                                    "borderRadius": "6px", "cursor": "pointer",
                                    "fontSize": "13px", "fontWeight": "bold",
                                    "marginRight": "8px",
                                }),
                                html.Button("⏭️ 跳过", id="history-node-skip-btn",
                                           n_clicks=0, disabled=True, style={
                                    "backgroundColor": "#6366f1", "color": "white",
                                    "border": "none", "padding": "10px 18px",
                                    "borderRadius": "6px", "cursor": "pointer",
                                    "fontSize": "13px", "fontWeight": "bold",
                                    "marginRight": "8px",
                                }),
                                html.Button("❌ 标记失败", id="history-node-fail-btn",
                                           n_clicks=0, disabled=True, style={
                                    "backgroundColor": "#ef4444", "color": "white",
                                    "border": "none", "padding": "10px 18px",
                                    "borderRadius": "6px", "cursor": "pointer",
                                    "fontSize": "13px", "fontWeight": "bold",
                                }),
                            ]),
                            dcc.Store(id="history-selected-node-store", data=""),
                            dcc.Store(id="history-dag-store", data={"nodes": [], "edges": []}),
                            dcc.Store(id="history-node-states-store"),
                        ]),
                        html.Div([
                            html.H3("🗺️ 流程图", style={"marginTop": "0"}),
                            html.Div([
                                html.Button("⊡ 适应窗口", id="history-fit-btn",
                                           n_clicks=0, style={
                                    **ZOOM_TOOLBAR_STYLE, "marginRight": "8px"
                                }),
                                html.Button("＋ 放大", id="history-zoom-in-btn",
                                           n_clicks=0, style={
                                    **ZOOM_TOOLBAR_STYLE, "marginRight": "4px"
                                }),
                                html.Button("－ 缩小", id="history-zoom-out-btn",
                                           n_clicks=0, style={
                                    **ZOOM_TOOLBAR_STYLE, "marginRight": "8px"
                                }),
                                html.Div(
                                    dcc.Slider(
                                        id="history-zoom-slider",
                                        min=0.2, max=4.0, step=0.1, value=0.6,
                                        marks={0.2: "20%", 0.6: "60%", 1: "100%",
                                               2: "200%", 4: "400%"},
                                        tooltip={"placement": "bottom", "always_visible": True},
                                    ),
                                    style={
                                        "width": "200px", "display": "inline-block",
                                        "verticalAlign": "middle",
                                    },
                                ),
                            ], style={"marginBottom": "10px"}),
                            cyto.Cytoscape(
                                id="history-flowchart",
                                elements=[],
                                layout={
                                    "name": "dagre", "rankDir": "TB",
                                    "nodeSep": 250, "rankSep": 380, "edgeSep": 60,
                                    "fit": True, "padding": 60,
                                },
                                style={
                                    "width": "100%", "height": "450px",
                                    "border": "1px solid #e2e8f0", "borderRadius": "12px",
                                    "marginBottom": "15px",
                                },
                                stylesheet=CYTO_STYLESHEET,
                                minZoom=0.2, maxZoom=4.0, zoom=0.6,
                                userZoomingEnabled=True,
                                userPanningEnabled=True,
                            ),
                            html.Div([
                                html.H4("🎯 节点详情", style={"margin": "0 0 8px", "fontSize": "14px"}),
                                html.Div(id="history-node-detail", children=[
                                    dcc.Markdown("**点击流程图中的节点查看详情**")
                                ], style={
                                    "padding": "10px", "border": "1px solid #e2e8f0",
                                    "borderRadius": "8px", "backgroundColor": "#f8fafc",
                                    "minHeight": "80px", "fontSize": "13px",
                                    "marginBottom": "10px",
                                }),
                            ], style={"marginBottom": "15px"}),
                            html.H3("📝 任务详情"),
                            html.Div(id="history-detail", style={
                                "whiteSpace": "pre-wrap", "padding": "15px",
                                "border": "1px solid #e2e8f0", "borderRadius": "8px",
                                "backgroundColor": "#f8fafc", "fontSize": "14px",
                                "lineHeight": "1.8",
                            }),
                        ], style={
                            "width": "68%", "display": "inline-block",
                            "padding": "20px", "verticalAlign": "top",
                        }),
                    ]),
                ]),
            ]),
        ]),
    ])


app.layout = build_layout()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  全局缓存
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_cyto_cache: Dict[str, Any] = {"hash": None, "elements": None}
_cyto_lock = threading.Lock()


def _stable_fingerprint(
    nodes: List[dict],
    edges: List[dict],
    node_states: Dict[str, str],
) -> Tuple[str, int]:
    try:
        node_ids = tuple(sorted(str(n.get("id", i)) for i, n in enumerate(nodes)))
        edge_keys = tuple(
            sorted((str(e["from"]), str(e["to"]), e.get("label", "")) for e in edges)
        )
        topo_part = f"{len(nodes)}|{len(edges)}|{node_ids}|{edge_keys}"
        state_part = json.dumps(node_states, sort_keys=True, ensure_ascii=False)
        raw = f"{topo_part}||{state_part}"
        raw_bytes = raw.encode("utf-8")
        return hashlib.md5(raw_bytes).hexdigest(), len(raw_bytes)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"Cache fingerprint failed: node_states contains non-serializable value. "
            f"All values must be str. Got error: {exc}"
        ) from exc


def _cytoscape_cached(
    nodes: List[dict],
    edges: List[dict],
    node_states: Dict[str, str],
) -> list:
    fingerprint, raw_size = _stable_fingerprint(nodes, edges, node_states)
    if raw_size > MAX_CACHE_INPUT_BYTES:
        return dag_to_cytoscape(nodes, edges, node_states)
    with _cyto_lock:
        if (
            fingerprint == _cyto_cache["hash"]
            and _cyto_cache["elements"] is not None
        ):
            return _cyto_cache["elements"]
        elements = dag_to_cytoscape(nodes, edges, node_states)
        _cyto_cache["hash"] = fingerprint
        _cyto_cache["elements"] = elements
        return elements


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  同步回调（纯前端状态，无 I/O）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@callback(
    Output("node-action-hint", "children"),
    Output("node-action-hint", "style"),
    Input("flowchart", "tapNodeData"),
    State("node-states-store", "data"),
    State("dag-store", "data"),
)
def on_node_hint(node_data, node_states, dag):
    from task_planner.infrastructure.constants import HINT_TEMPLATES
    from task_planner.infrastructure.ui_styles import HINT_STYLES
    from task_planner.core.graph.state import NodeStatus
    _STYLES = HINT_STYLES
    if not dag or not dag.get("nodes"):
        return (
            HINT_TEMPLATES.get("loading", "⏳ 加载中..."),
            _STYLES.get("loading", {"color": "#94a3b8", "fontSize": "14px"}),
        )
    if not node_data:
        return HINT_TEMPLATES["ready"], _STYLES["ready"]
    nid = str(node_data.get("id", ""))
    raw_st = node_states.get(nid, NodeStatus.PENDING)
    try:
        status = NodeStatus(raw_st)
    except (ValueError, KeyError):
        status = NodeStatus.PENDING
    template_key = status.value
    text = HINT_TEMPLATES.get(template_key, HINT_TEMPLATES["pending"]).format(nid=nid)
    style = _STYLES.get(template_key, _STYLES["pending"])
    return text, style


@app.callback(
    Output("flowchart", "className"),
    Input("flowchart", "mouseoverNodeData"),
    Input("flowchart", "mouseoutNodeData"),
    prevent_initial_call=True,
)
def on_node_hover(over_data, out_data):
    ctx = dash.callback_context.triggered_id
    if ctx == "flowchart" and over_data:
        return dash.Patch({"addClasses": f"#node-{over_data['id']}.node-hover"})
    return dash.no_update


@callback(
    Output("node-detail-panel", "children"),
    Output("selected-node-store", "data"),
    Input("flowchart", "tapNodeData"),
    State("dag-store", "data"),
    State("node-states-store", "data"),
)
def on_tap_node(node_data, dag, node_states):
    if not node_data or not node_data.get("id"):
        return dcc.Markdown("👆 **点击流程图中的节点查看详情并操作**",
                            style={"fontSize": "14px", "color": "#94a3b8"}), no_update
    nid = str(node_data["id"])
    node_states = node_states or {}
    st = str(node_states.get(nid, "pending"))
    md_text = build_detail_markdown(dict(node_data))
    current_label = STATE_LABELS.get(st, st)

    def make_btn(label, btn_id, state_key):
        bg, color = STATE_COLORS.get(state_key, ("#e2e8f0", "#475569"))
        is_active = (st == state_key)
        style = {
            **BASE_BTN_STYLE,
            "backgroundColor": bg if is_active else "#e2e8f0",
            "color": color if is_active else "#475569",
            "opacity": "1" if is_active else "0.8",
        }
        return html.Button(label, id=btn_id, n_clicks=0, style=style)

    buttons = [
        make_btn("✅ 完成", "complete-btn", "done"),
        make_btn("⏭️ 跳过", "skip-btn-panel", "skipped"),
        make_btn("❌ 失败", "fail-btn-panel", "failed"),
        html.Button("🔄 重置", id="reset-btn-panel", n_clicks=0,
                    style={**BASE_BTN_STYLE, "backgroundColor": "#e2e8f0", "color": "#475569"}),
    ]
    panel_children = [
        html.Div(f"当前状态: {current_label}", style={
            "fontSize": "13px", "color": "#64748b", "marginBottom": "8px", "fontWeight": "bold",
        }),
        dcc.Markdown(md_text, style={"fontSize": "14px", "lineHeight": "1.8"}),
        html.Div(buttons, style={"marginTop": "12px"}),
    ]
    return html.Div(panel_children), nid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  异步回调（涉及后端 I/O）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@callback(
    Output("thread-id-store", "data"), Output("stream-interval", "disabled"),
    Output("stream-active", "data"), Output("global-status", "children"),
    Output("flowchart", "elements"), Output("submit-btn", "disabled"),
    Output("stop-btn", "disabled"), Output("start-exec-btn", "disabled"),
    Output("dag-store", "data"), Output("node-states-store", "data"),
    Output("progress-bar-fill", "style"), Output("task-start-time", "data"),
    Input("submit-btn", "n_clicks"),
    State("user-input", "value"), State("enable-refine", "value"),
    prevent_initial_call=True,
)
async def on_submit(n_clicks, user_input, refine_value):
    from task_planner.infrastructure.cog import hub as _hub
    from task_planner.infrastructure.ui_styles import (
        EMPTY_BAR_STYLE,
        EMPTY_DAG,
        EMPTY_STATES,
    )
    MAX_INPUT_LENGTH = _hub.dev.MAX_INPUT_LENGTH
    if not user_input or not user_input.strip():
        return ("", True, False, "⚠️ 请输入任务需求", [], False, True, True,
                EMPTY_DAG, EMPTY_STATES, EMPTY_BAR_STYLE, 0.0)

    cleaned = user_input.strip()
    if len(cleaned) > MAX_INPUT_LENGTH:
        return ("", True, False, f"⚠️ 输入过长（{len(cleaned)}字），请精简至 {MAX_INPUT_LENGTH} 字以内",
                [], False, True, True, EMPTY_DAG, EMPTY_STATES, EMPTY_BAR_STYLE, 0.0)

    thread_id = str(uuid.uuid4())
    enable_refine = isinstance(refine_value, list) and "refine" in refine_value

    try:
        await start_stream(
            thread_id=thread_id,
            user_input=cleaned,
            enable_refine=enable_refine,
            run_task_stream_fn=run_task_stream,
        )
    except Exception as e:
        logger.exception("start_stream 失败")
        return ("", True, False, f"❌ 任务启动失败: {e}", [], False, True, True,
                EMPTY_DAG, EMPTY_STATES, EMPTY_BAR_STYLE, 0.0)

    return (
        thread_id,
        False,
        True,
        "🚀 任务已提交，正在生成流程图...",
        [],
        True,
        False,
        True,
        EMPTY_DAG,
        EMPTY_STATES,
        EMPTY_BAR_STYLE,
        time.time(),
    )


@callback(
    Output("flowchart", "elements", allow_duplicate=True),
    Output("global-status", "children", allow_duplicate=True),
    Output("stream-interval", "disabled", allow_duplicate=True),
    Output("submit-btn", "disabled", allow_duplicate=True),
    Output("stop-btn", "disabled", allow_duplicate=True),
    Output("start-exec-btn", "disabled", allow_duplicate=True),
    Output("dag-store", "data", allow_duplicate=True),
    Output("progress-bar-fill", "style", allow_duplicate=True),
    Input("stream-interval", "n_intervals"),
    State("thread-id-store", "data"),
    State("stream-active", "data"),
    State("task-start-time", "data"),
    prevent_initial_call=True,
)
async def poll_stream_progress(n_intervals, thread_id, stream_active, task_start_time):
    now = time.time()
    elapsed = _safe_elapsed(task_start_time, now)

    if not stream_active or not thread_id:
        return (no_update, no_update, True, False, True, True,
                no_update, EMPTY_BAR_MINI_STYLE)

    state = await get_stream_state(thread_id)
    if not state:
        return (no_update, f"⚠️ 任务状态丢失 (⏱️ {elapsed}s)", True, False, True, True,
                no_update, EMPTY_BAR_MINI_STYLE)

    latest = await state.get_latest()
    direct_response = latest.get("direct_response", "")
    if direct_response:
        msg = f"💬 {direct_response}\n\n⏱️ 总耗时 {elapsed}s"
        return ([], dcc.Markdown(msg, style=MARKDOWN_PRE_STYLE),
                True, False, True, True, no_update,
                EMPTY_BAR_STYLE)

    nodes = latest.get("nodes") or []
    edges = latest.get("edges") or []
    node_states = latest.get("node_states") or {}
    dag = {"nodes": nodes, "edges": edges}
    elements = _cytoscape_cached(nodes, edges, node_states)

    status_text = str(latest.get("status_text", ""))
    if not status_text:
        steps = latest.get("steps", [])
        status_text = steps[-1] if steps else "处理中..."
    status_text = f"{status_text} (⏱️ {elapsed}s)"

    if state.finished:
        if state.error:
            return (
                elements,
                f"❌ 任务失败: {state.error} (⏱️ {elapsed}s)",
                True, False, True, True, dag,
                BAR_ERROR,
            )
        return (
            elements,
            "✅ 流程图渲染完成！请先在图中点击一个高亮（黄色）节点，再选择操作",
            True, False, True, False, dag,
            BAR_DONE,
        )

    return (elements, status_text, False, True, False, True, dag,
            BAR_LOADING)


@callback(
    Output("stream-interval", "disabled", allow_duplicate=True),
    Output("global-status", "children", allow_duplicate=True),
    Output("submit-btn", "disabled", allow_duplicate=True),
    Output("stop-btn", "disabled", allow_duplicate=True),
    Output("resume-btn", "disabled"),
    Input("stop-btn", "n_clicks"),
    State("thread-id-store", "data"),
    prevent_initial_call=True,
)
async def on_stop(n_clicks, thread_id):
    if not thread_id:
        return (True, "⚠️ 没有正在运行的任务", False, True, True)

    try:
        await cancel_stream(thread_id)
        status_msg = "⏹️ 任务已取消（后端 LLM 调用将在下次响应前终止）"
        resume_disabled = True
    except Exception as e:
        logger.exception(f"Failed to cancel stream {thread_id}")
        status_msg = f"❌ 取消失败: {e}（任务可能仍在后台运行）"
        resume_disabled = True

    return (
        True,
        status_msg,
        False,
        True,
        resume_disabled,
    )


@callback(
    Output("node-states-store", "data", allow_duplicate=True),
    Output("flowchart", "elements", allow_duplicate=True),
    Output("global-status", "children", allow_duplicate=True),
    Output("start-exec-btn", "disabled", allow_duplicate=True),
    Input("start-exec-btn", "n_clicks"),
    State("dag-store", "data"),
    State("node-states-store", "data"),
    prevent_initial_call=True,
)
def on_start_execution(n_clicks, dag, node_states):
    if not dag or not dag.get("nodes"):
        return no_update, no_update, "⚠️ 没有可执行的任务", True

    nodes = dag["nodes"]
    edges = dag.get("edges", [])
    valid_ids = {str(n["id"]) for n in nodes}
    in_deg = {}
    for e in edges:
        target = str(e.get("to", ""))
        if target in valid_ids:
            in_deg[target] = in_deg.get(target, 0) + 1

    new_states = dict(node_states or {})
    for node in nodes:
        nid = str(node["id"])
        if nid not in new_states and in_deg.get(nid, 0) == 0:
            new_states[nid] = "running"

    elements = _cytoscape_cached(nodes, edges, new_states)
    running_count = sum(1 for v in new_states.values() if v == "running")
    if running_count > 0:
        status_msg = f"▶️ 正在执行 {running_count} 个根节点，完成后点击节点标记状态"
    else:
        status_msg = "⚠️ 未找到可执行的根节点（可能存在循环依赖），请检查流程图"

    return new_states, elements, status_msg, True


# ── 节点操作（前端状态更新，无 I/O，但为了统一风格，也改为 async）──
@callback(
    Output("node-states-store", "data", allow_duplicate=True),
    Output("flowchart", "elements", allow_duplicate=True),
    Output("global-status", "children", allow_duplicate=True),
    Output("progress-bar-fill", "style", allow_duplicate=True),
    Output("start-exec-btn", "disabled", allow_duplicate=True),
    Input("complete-btn", "n_clicks"),
    State("selected-node-store", "data"),
    State("dag-store", "data"),
    State("node-states-store", "data"),
    prevent_initial_call=True,
)
async def on_complete_node(n_clicks, selected_nid, dag, node_states):
    if not selected_nid or not dag:
        return (no_update,) * 5

    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    new_states = dict(node_states or {})
    new_states[selected_nid] = "done"
    new_states = _auto_unlock_downstream(dag, new_states, selected_nid, "done")
    elements = _cytoscape_cached(nodes, edges, new_states)

    done_count = sum(1 for v in new_states.values() if v == "done")
    total = len(nodes)
    pct = int(done_count / total * 100) if total > 0 else 0
    status_msg = f"✅ 完成 {done_count}/{total} ({pct}%)"
    if pct >= 100:
        status_msg = "🎉 所有节点已完成！"

    bar_style = {
        "width": f"{pct}%",
        "height": "100%",
        "background": "linear-gradient(90deg, #10b981, #34d399)",
        "borderRadius": "6px",
        "transition": "width 0.3s ease",
    }
    return new_states, elements, status_msg, bar_style, False


@callback(
    Output("node-states-store", "data", allow_duplicate=True),
    Output("flowchart", "elements", allow_duplicate=True),
    Output("global-status", "children", allow_duplicate=True),
    Output("progress-bar-fill", "style", allow_duplicate=True),
    Output("start-exec-btn", "disabled", allow_duplicate=True),
    Input("skip-btn", "n_clicks"),
    Input("skip-btn-panel", "n_clicks"),
    State("selected-node-store", "data"),
    State("dag-store", "data"),
    State("node-states-store", "data"),
    prevent_initial_call=True,
)
async def on_skip_node(n1, n2, selected_nid, dag, node_states):
    triggered_id = ctx.triggered_id
    if triggered_id not in ("skip-btn", "skip-btn-panel"):
        return (no_update,) * 5

    if not selected_nid or not dag:
        return no_update, no_update, "⚠️ 请先选择要跳过的节点", no_update, no_update

    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    new_states = dict(node_states or {})
    new_states[selected_nid] = "skipped"
    new_states = _auto_unlock_downstream(dag, new_states, selected_nid, "skipped")
    elements = _cytoscape_cached(nodes, edges, new_states)

    done_count = sum(1 for v in new_states.values() if v == "done")
    skipped_count = sum(1 for v in new_states.values() if v == "skipped")
    total = len(nodes)
    processed = done_count + skipped_count
    pct = int(processed / total * 100) if total > 0 else 0

    status_msg = f"⏭️ 已跳过 {selected_nid}（下游已按规则解锁）| ✅{done_count} ⏭️{skipped_count} / {total}"
    bar_style = {
        "width": f"{pct}%",
        "height": "100%",
        "background": "linear-gradient(90deg, #6366f1, #818cf8)",
        "borderRadius": "6px",
        "transition": "width 0.3s ease",
    }
    return new_states, elements, status_msg, bar_style, False


@callback(
    Output("node-states-store", "data", allow_duplicate=True),
    Output("flowchart", "elements", allow_duplicate=True),
    Output("global-status", "children", allow_duplicate=True),
    Output("progress-bar-fill", "style", allow_duplicate=True),
    Output("start-exec-btn", "disabled", allow_duplicate=True),
    Input("fail-btn", "n_clicks"),
    Input("fail-btn-panel", "n_clicks"),
    State("selected-node-store", "data"),
    State("dag-store", "data"),
    State("node-states-store", "data"),
    prevent_initial_call=True,
)
async def on_fail_node(n1, n2, selected_nid, dag, node_states):
    triggered_id = ctx.triggered_id
    if triggered_id not in ("fail-btn", "fail-btn-panel"):
        return (no_update,) * 5

    if not selected_nid or not dag:
        return no_update, no_update, "⚠️ 请先选择要标记失败的节点", no_update, no_update

    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    new_states = dict(node_states or {})
    new_states[selected_nid] = "failed"
    new_states = _auto_unlock_downstream(
        dag, new_states, selected_nid, "failed",
        allowed_edge_types=("soft", "optional")
    )
    elements = _cytoscape_cached(nodes, edges, new_states)

    done_count = sum(1 for v in new_states.values() if v == "done")
    skipped_count = sum(1 for v in new_states.values() if v == "skipped")
    failed_count = sum(1 for v in new_states.values() if v == "failed")
    total = len(nodes)
    processed = done_count + skipped_count + failed_count
    pct = int(processed / total * 100) if total > 0 else 0

    hard_blocked_hint = "（强依赖下游已锁定）"
    status_msg = f"❌ {selected_nid} 标记为「做不到」{hard_blocked_hint} | ✅{done_count} ⏭️{skipped_count} ❌{failed_count}/{total}"
    bar_style = {
        "width": f"{pct}%",
        "height": "100%",
        "background": "linear-gradient(90deg, #ef4444, #f87171)",
        "borderRadius": "6px",
        "transition": "width 0.3s ease",
    }
    return new_states, elements, status_msg, bar_style, False


@callback(
    Output("node-states-store", "data", allow_duplicate=True),
    Output("flowchart", "elements", allow_duplicate=True),
    Output("global-status", "children", allow_duplicate=True),
    Output("progress-bar-fill", "style", allow_duplicate=True),
    Output("start-exec-btn", "disabled", allow_duplicate=True),
    Input("reset-btn-panel", "n_clicks"),
    State("selected-node-store", "data"),
    State("dag-store", "data"),
    State("node-states-store", "data"),
    State("thread-id-store", "data"),
    prevent_initial_call=True,
)
async def on_reset_node(n_clicks, selected_nid, dag, node_states, task_id):
    if ctx.triggered_id != "reset-btn-panel":
        return (no_update,) * 5

    if not selected_nid or not dag:
        return no_update, no_update, "⚠️ 请先选择要重置的节点", no_update, no_update

    try:
        nid_int = int(selected_nid)
    except (ValueError, TypeError):
        return no_update, no_update, f"⚠️ 无效节点ID: {selected_nid}", no_update, no_update

    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    old_states = dict(node_states or {})

    db_ok = False
    has_task = bool(task_id)
    if has_task:
        try:
            db_ok = await reset_node_status(task_id, nid_int)  

        except Exception as e:
            logger.error("[Reset] DB 重置失败 task=%s node=%s: %s", task_id, nid_int, e, exc_info=True)

    new_states = dict(old_states)
    frontend_reset = False

    if has_task and not db_ok:
        pass
    else:
        if selected_nid in new_states:
            del new_states[selected_nid]
            frontend_reset = True

    elements = _cytoscape_cached(nodes, edges, new_states)

    done_count = sum(1 for v in new_states.values() if v == "done")
    skipped_count = sum(1 for v in new_states.values() if v == "skipped")
    failed_count = sum(1 for v in new_states.values() if v == "failed")
    total = len(nodes)
    processed = done_count + skipped_count + failed_count
    pct = int(processed / total * 100) if total > 0 else 0

    bar_style = {
        "width": f"{pct}%",
        "height": "100%",
        "background": "linear-gradient(90deg, #6366f1, #818cf8)",
        "borderRadius": "6px",
        "transition": "width 0.3s ease",
    }

    if has_task and not db_ok:
        msg = f"❌ 节点 {selected_nid} 后端重置失败，前端状态未变更（请重试或检查日志）"
    elif frontend_reset:
        sync_label = "前端+后端同步" if has_task else "仅前端，无关联任务"
        msg = f"🔄 节点 {selected_nid} 已重置（{sync_label}）| ✅{done_count} ⏭️{skipped_count} ❌{failed_count}/{total}"
    else:
        msg = f"ℹ️ 节点 {selected_nid} 原本就是初始状态，无需重置"

    return new_states, elements, msg, bar_style, False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  历史记录 Callbacks（异步）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@callback(
    Output("history-dropdown", "options"),
    Output("history-dropdown", "placeholder"),
    Input("refresh-btn", "n_clicks"),
    Input("main-tabs", "value"),
    prevent_initial_call=True,
)
async def load_history_list(n_clicks, tab_value):
    if tab_value != "tab-history":
        return no_update, no_update

    try:
        tasks = await list_tasks(50)
    except Exception as e:
        logger.error("[History] 加载任务列表失败: %s", e, exc_info=True)
        return [], "❌ 加载失败，请检查后端服务"

    if not tasks:
        return [], "暂无历史记录"

    sorted_tasks = sorted(tasks, key=lambda t: t.get("created", ""), reverse=True)    
    options = []
    for t in sorted_tasks:
        raw_title = t.get("title") or ""
        safe_title = str(raw_title)[:30] if raw_title else "(无标题)"
        tid_suffix = str(t.get("task_id", ""))[-8:]
        options.append({
            "label": f"{safe_title} | {tid_suffix}",
            "value": t["task_id"],
        })
    return options, "请选择历史任务"


@callback(
    Output("history-flowchart", "elements"),
    Output("history-detail", "children"),
    Output("history-flowchart", "pan"),
    Output("global-status", "children", allow_duplicate=True),
    Output("dag-store", "data", allow_duplicate=True),
    Output("history-replan-btn", "disabled"),
    Output("history-edit-plan-btn", "disabled"),
    Output("history-resume-btn", "disabled"),
    Output("history-retry-btn", "disabled"),
    Output("history-action-status", "children"),
    Output("history-dag-store", "data"),
    Output("history-node-states-store", "data"),
    Input("history-dropdown", "value"),
    prevent_initial_call=True,
)
async def on_history_select(task_ids):
    if not task_ids:
        return HistorySelectResult().to_tuple()

    task_id = task_ids[0] if isinstance(task_ids, list) else task_ids

    try:
        data = await load_task_with_plan(task_id)
    except Exception as e:
        logger.error("[History] 加载任务失败 task=%s: %s", task_id, e, exc_info=True)
        return HistorySelectResult(
            global_status="❌ 加载失败，请检查后端服务",
            action_status="❌ 加载失败",
        ).to_tuple()

    if not data:
        return HistorySelectResult(
            global_status="⚠️ 任务不存在",
            action_status="⚠️ 任务不存在",
        ).to_tuple()

    task_info = data["task"]
    plan_data = data.get("plan")

    if not plan_data:
        direct_resp = task_info.get("direct_response") or "（无回答内容）"
        raw_query = task_info.get("raw_query") or ""
        md_text = (
            f"## 💬 直接回答\n\n"
            f"**用户提问：**\n\n{raw_query}\n\n---\n\n"
            f"**AI 回答：**\n\n{direct_resp}\n\n---\n\n"
            f"*任务 ID: {task_id} | 类型: 直接回答（无规划流程）*"
        )
        return HistorySelectResult(
            detail=dcc.Markdown(md_text, style={"fontSize": "14px", "lineHeight": "1.8"}),
            global_status="💬 直接回答任务已加载",
            action_status="💡 这是一个直接回答类会话，无需规划流程。可在新建任务中重新提问。",
        ).to_tuple()

    nodes = plan_data.get("nodes", [])
    edges = plan_data.get("edges", [])

    node_states = {
        str(n.get("id", "")): NODE_STATUS_CODE_MAP.get(n.get("status", 0), "pending")
        for n in nodes
    }

    elements = _cytoscape_cached(nodes, edges, node_states)
    md_text = build_history_detail_markdown(task_id, nodes)
    dag_store_data = {"nodes": nodes, "edges": edges, "task_id": task_id}

    has_failed = any(v == "failed" for v in node_states.values())
    has_pending = any(v in ("pending", "blocked") for v in node_states.values())
    all_done = all(v in ("done", "skipped") for v in node_states.values())

    pending_count = sum(1 for v in node_states.values() if v in ("pending", "blocked"))
    hint_parts = []
    if all_done:
        hint_parts.append("✅ 所有节点已完成")
    if has_failed:
        hint_parts.append("❌ 存在失败节点，可重试")
    if has_pending:
        hint_parts.append(f"⏳ 还有 {pending_count} 个节点待执行")
    action_hint = " | ".join(hint_parts) if hint_parts else "✅ 任务状态正常"

    return HistorySelectResult(
        elements=elements,
        detail=dcc.Markdown(md_text, style={"fontSize": "14px", "lineHeight": "1.8"}),
        global_status=f"✅ 已加载历史流程图（{len(nodes)} 个节点）",
        dag_store=dag_store_data,
        replan_disabled=False,
        edit_disabled=False,
        resume_disabled=not has_pending,
        retry_disabled=not has_failed,
        action_status=action_hint,
        history_dag_store=dag_store_data,
        history_node_states=node_states,
    ).to_tuple()


@callback(
    Output("history-node-detail", "children"),
    Output("history-selected-node-store", "data"),
    Output("history-node-done-btn", "disabled"),
    Output("history-node-skip-btn", "disabled"),
    Output("history-node-fail-btn", "disabled"),
    Input("history-flowchart", "tapNodeData"),
    State("history-node-states-store", "data"),
    prevent_initial_call=True,
)
def on_history_tap_node(node_data, node_states):
    if not node_data:
        return HistoryTapNodeResult().to_tuple()

    nid = str(node_data.get("id", ""))
    if not nid:
        logger.warning("[History] tapNodeData 缺少 id 字段: %s", node_data)
        return HistoryTapNodeResult().to_tuple()

    node_states = node_states or {}
    current_state = str(node_states.get(nid, "pending"))
    can_operate = current_state in NODE_OPERABLE_STATES

    try:
        clean_data = sanitize_node_for_user(dict(node_data))
        md_text = build_detail_markdown(clean_data)
    except Exception as e:
        logger.error("[History] 构建节点详情失败 node=%s: %s", nid, e, exc_info=True)
        md_text = f"**⚠️ 节点详情加载失败**\n\n`{nid}`"

    return HistoryTapNodeResult(
        detail=dcc.Markdown(md_text, style={"fontSize": "13px", "lineHeight": "1.8"}),
        selected_node=nid,
        done_disabled=not can_operate,
        skip_disabled=not can_operate,
        fail_disabled=not can_operate,
    ).to_tuple()


@callback(
    Output("history-dropdown", "options", allow_duplicate=True),
    Output("history-dropdown", "value", allow_duplicate=True),
    Output("delete-status", "children"),
    Input("delete-btn", "n_clicks"),
    State("history-dropdown", "value"),
    prevent_initial_call=True,
)
async def on_delete(n_clicks, task_ids):
    if not task_ids:
        return no_update, no_update, "⚠️ 请先选择要删除的任务"

    ids = task_ids if isinstance(task_ids, list) else [task_ids]

    try:
        count = await batch_delete_tasks(ids)
        logger.info("[History] 删除成功 tasks=%s count=%d", ids, count)
    except Exception as e:
        logger.error("[History] 删除失败 tasks=%s: %s", ids, e, exc_info=True)
        return no_update, no_update, "❌ 删除失败，请稍后重试或联系管理员"

    try:
        tasks = await list_tasks(50)
        options = build_history_dropdown_options(tasks)
    except Exception as e:
        logger.error("[History] 删除后刷新列表失败: %s", e, exc_info=True)
        return no_update, None, f"✅ 已删除 {count} 个任务，但列表刷新失败，请手动刷新页面"

    return options, None, f"✅ 已删除 {count} 个任务"


@callback(
    Output("global-status", "children", allow_duplicate=True),
    Output("main-tabs", "value"),
    Output("user-input", "value"),
    Input("history-replan-btn", "n_clicks"),
    State("history-dropdown", "value"),
    prevent_initial_call=True,
)
async def on_history_replan(n_clicks, task_ids):
    msg, tab, query = await _load_and_fill_query(task_ids, action_label="重新规划")
    return msg, tab, query


@callback(
    Output("history-node-states-store", "data", allow_duplicate=True),
    Output("history-flowchart", "elements", allow_duplicate=True),
    Output("history-action-status", "children", allow_duplicate=True),
    Input("history-node-done-btn", "n_clicks"),
    State("history-selected-node-store", "data"),
    State("history-dag-store", "data"),
    State("history-node-states-store", "data"),
    prevent_initial_call=True,
)
def on_history_node_done(n_clicks, selected_nid, dag, node_states):
    if not selected_nid or not node_states or not dag:
        return no_update, no_update, no_update

    try:
        new_states, elements, status_msg = update_node_and_render(
            dag, node_states, selected_nid, target_status="done"
        )
    except Exception as e:
        logger.error("[NodeAction] 标记完成失败 node=%s: %s", selected_nid, e, exc_info=True)
        return no_update, no_update, f"❌ 状态更新失败: {selected_nid}"

    logger.info("[NodeAction] 手动标记完成 node=%s", selected_nid)
    return new_states, elements, status_msg


@callback(
    Output("history-node-states-store", "data", allow_duplicate=True),
    Output("history-flowchart", "elements", allow_duplicate=True),
    Output("history-action-status", "children", allow_duplicate=True),
    Input("history-node-skip-btn", "n_clicks"),
    State("history-selected-node-store", "data"),
    State("history-dag-store", "data"),
    State("history-node-states-store", "data"),
    prevent_initial_call=True,
)
def on_history_node_skip(n_clicks, selected_nid, dag, node_states):
    if not selected_nid or not node_states or not dag:
        return no_update, no_update, no_update

    try:
        new_states, elements, status_msg = update_node_and_render(
            dag, node_states, selected_nid, target_status="skipped"
        )
    except Exception as e:
        logger.error("[NodeAction] 跳过失败 node=%s: %s", selected_nid, e, exc_info=True)
        return no_update, no_update, f"❌ 跳过操作失败: {selected_nid}"

    logger.info("[NodeAction] 手动跳过 node=%s", selected_nid)
    return new_states, elements, status_msg


@callback(
    Output("history-node-states-store", "data", allow_duplicate=True),
    Output("history-flowchart", "elements", allow_duplicate=True),
    Output("history-action-status", "children", allow_duplicate=True),
    Input("history-node-fail-btn", "n_clicks"),
    State("history-selected-node-store", "data"),
    State("history-dag-store", "data"),
    State("history-node-states-store", "data"),
    prevent_initial_call=True,
)
def on_history_node_fail(n_clicks, selected_nid, dag, node_states):
    if not selected_nid or not node_states or not dag:
        return no_update, no_update, no_update

    try:
        new_states, elements, status_msg = update_node_and_render(
            dag, node_states, selected_nid, target_status="failed"
        )
    except Exception as e:
        logger.error("[NodeAction] 标记失败异常 node=%s: %s", selected_nid, e, exc_info=True)
        return no_update, no_update, f"❌ 标记失败操作异常: {selected_nid}"

    logger.warning("[NodeAction] 手动标记失败 node=%s", selected_nid)
    return new_states, elements, status_msg


@callback(
    Output("global-status", "children", allow_duplicate=True),
    Output("main-tabs", "value", allow_duplicate=True),
    Output("user-input", "value", allow_duplicate=True),
    Input("history-resume-btn", "n_clicks"),
    State("history-dropdown", "value"),
    prevent_initial_call=True,
)
async def on_history_resume(n_clicks, task_ids):
    msg, tab, query = await _load_and_fill_query(task_ids, action_label="继续执行")
    return msg, tab, query


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  数据导出（JSON / DOT / HTML）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _escape_dot_label(text: str) -> str:
    if not text:
        return ""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _export_json(nodes, edges, task_id_str):
    content = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2)
    return dcc.send_string(
        content,
        filename=_make_filename("dag", task_id_str, "json"),
        type="application/json"
    )


def _export_dot(nodes, edges, task_id_str):
    lines = ["digraph G {", '  rankdir=TB;', '  node [shape=box, style="rounded,filled", fillcolor="#E8F4FD"];']
    for n in nodes:
        node_id = _escape_dot_label(n.get("id", ""))
        label = _escape_dot_label(n.get("name") or n.get("node_name", ""))
        lines.append(f'  "{node_id}" [label="{label}"];')
    for e in edges:
        from_id = _escape_dot_label(e.get("from", ""))
        to_id = _escape_dot_label(e.get("to", ""))
        label = _escape_dot_label(e.get("label", ""))
        label_attr = f' [label="{label}"]' if label else ""
        lines.append(f'  "{from_id}" -> "{to_id}"{label_attr};')
    lines.append("}")
    return dcc.send_string(
        "\n".join(lines),
        filename=_make_filename("dag", task_id_str, "dot"),
        type="text/vnd.graphviz"
    )


def _export_html(nodes, edges, task_id_str):
    html_content = _generate_html_export(nodes, edges, task_id_str)
    return dcc.send_string(
        html_content,
        filename=_make_filename("dag", task_id_str, "html"),
        type="text/html"
    )


_DATA_EXPORT_STRATEGIES = {
    "json": _export_json,
    "dot":  _export_dot,
    "html": _export_html,
}


@callback(
    Output("export-download", "data"),
    Input("export-data-btn", "n_clicks"),
    State("history-dropdown", "value"),
    State("dag-store", "data"),
    State("data-format", "value"),
    prevent_initial_call=True,
)
async def on_export_data(n_clicks, history_task_ids, dag_store, fmt):
    nodes, edges, node_states, task_id_str = await _resolve_dag_data(
        history_task_ids, dag_store, node_states
    )

    if not nodes:
        logger.warning("[Export] 没有节点数据可导出 (format=%s)", fmt)
        return None

    export_fn = _DATA_EXPORT_STRATEGIES.get(fmt)
    if not export_fn:
        logger.error("[Export] 不支持的导出格式: %s", fmt)
        return None

    try:
        # 导出函数是同步的，用 to_thread 包裹
        result = await asyncio.to_thread(export_fn, nodes, edges, task_id_str)
        logger.info("[Export] ✅ %s 导出成功 (task=%s)", fmt.upper(), task_id_str)
        return result
    except Exception as e:
        logger.error("[Export] %s 导出失败: %s", fmt.upper(), e, exc_info=True)
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  图片导出（PNG / SVG）—— 异步 + to_thread
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@callback(
    Output("export-download", "data", allow_duplicate=True),
    Input("export-image-btn", "n_clicks"),
    State("history-dropdown", "value"),
    State("dag-store", "data"),
    State("node-states-store", "data"),
    State("image-format", "value"),
    prevent_initial_call=True,
)
async def on_export_image(n_clicks, history_task_ids, dag_store, node_states, image_format):
    nodes, edges, node_states, task_id_str = await _resolve_dag_data(
        history_task_ids, dag_store, node_states
    )

    if not nodes:
        logger.warning("[Export] 没有节点数据可导出 (format=%s)", image_format)
        return None

    title = f"流程图 {task_id_str}"

    try:
        if image_format == "svg":
            svg_bytes = await asyncio.to_thread(
                export_dag_to_svg,
                nodes=nodes, edges=edges,
                output_path=None,
                node_states=node_states,
                title=title, dpi=200,
            )
            logger.info("[Export] ✅ SVG 导出成功 (%.1f KB, task=%s)",
                       len(svg_bytes) / 1024, task_id_str)
            return dcc.send_bytes(
                svg_bytes,
                filename=_make_filename("flowchart", task_id_str, "svg"),
                type="image/svg+xml"
            )

        else:  # 默认 PNG
            png_bytes = await asyncio.to_thread(
                export_dag_to_png,
                nodes=nodes, edges=edges,
                output_path=None,
                node_states=node_states,
                title=title, width=1600, height=1200, dpi=200,
            )
            logger.info("[Export] ✅ PNG 导出成功 (%.1f KB, task=%s)",
                       len(png_bytes) / 1024, task_id_str)
            return dcc.send_bytes(
                png_bytes,
                filename=_make_filename("flowchart", task_id_str, "png"),
                type="image/png"
            )

    except Exception as e:
        logger.error("[Export] %s 图片渲染失败: %s",
                    image_format.upper(), e, exc_info=True)
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  图操作 Clientside Callbacks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

for _key, _cfg in GRAPH_CONFIGS.items():
    clientside_callback(
        FIT_JS_TEMPLATE.format(
            element_id=_cfg["element_id"],
            layout_json=json.dumps(_cfg["layout_options"]),
        ),
        Output(_cfg["fit_btn_id"], "n_clicks"),
        Input(_cfg["fit_btn_id"], "n_clicks"),
    )

    clientside_callback(
        ZOOM_BTN_JS_TEMPLATE.format(
            element_id=_cfg["element_id"],
            zoom_in_btn_id=_cfg["zoom_in_btn_id"],
            zoom_out_btn_id=_cfg["zoom_out_btn_id"],
        ),
        Output(_cfg["zoom_slider_id"], "value"),
        Input(_cfg["zoom_in_btn_id"], "n_clicks"),
        Input(_cfg["zoom_out_btn_id"], "n_clicks"),
    )

    clientside_callback(
        ZOOM_SLIDER_JS_TEMPLATE.format(element_id=_cfg["element_id"]),
        Output(_cfg["element_id"], "zoom"),
        Input(_cfg["zoom_slider_id"], "value"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  启动
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _validate_port(port_value) -> int:
    try:
        port = int(port_value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"DASH_PORT 无法转换为整数: {port_value!r}") from e
    if port not in VALID_PORT_RANGE:
        raise ValueError(f"DASH_PORT={port} 超出合法范围 [1-65535]")
    return port


def main() -> None:
    host = DASH_HOST
    debug = DEBUG

    try:
        port = _validate_port(DASH_PORT)
    except ValueError as e:
        logger.error("[Startup] 配置校验失败: %s", e)
        sys.exit(1)

    boost(
        app=app,
        graceful_shutdown=True,
        memory_watchdog=not debug,
        # csp_policy=csp
    )

    mode = "debug" if debug else "production"
    is_reloader = WERKZEUG_RUN_MAIN == "true"
    process_tag = " [reloader]" if is_reloader else ""

    logger.info(
        "[Startup] Dash starting (%s%s) | %s:%d",
        mode, process_tag, host, port,
    )

    app.run(debug=debug, host=host, port=port)


if __name__ == "__main__":
    main()
