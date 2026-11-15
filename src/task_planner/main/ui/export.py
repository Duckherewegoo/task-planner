"""export.py — 数据/图片导出（UI 适配层）

职责：
  接收 Dash 回调传进来的数据，调用底层渲染引擎（cairoviz_export），
  或进行纯数据序列化（JSON/HTML），最终统一封装成 Dash 的 send_string / send_bytes。

⚠️ 本模块不负责底层图形渲染——渲染全部委托给 utils.cairoviz_export。
"""

import json
import textwrap
from collections.abc import Callable

from dash import dcc

from task_planner.infrastructure.logger_setup import get_logger
from task_planner.utils.cairoviz_export import (
    export_dag_to_dot,
    export_dag_to_png,
    export_dag_to_svg,
)

from .data_ops import make_filename

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 纯数据导出：JSON / HTML
# ═══════════════════════════════════════════════════════════════════
def export_json(nodes, edges, task_id_str, **kwargs):
    """导出原始 JSON 数据"""
    content = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2)
    return dcc.send_string(
        content,
        filename=make_filename("dag", task_id_str, "json"),
        type="application/json",
    )


def generate_html_export(nodes, edges, task_id) -> str:
    """生成内嵌 Cytoscape 的独立 HTML（交互式，依赖 CDN JS）"""
    elements_list = []
    for i, n in enumerate(nodes):
        nid = str(n.get("id", i))
        label = n.get("name", n.get("node_name", f"Node {i}"))
        detail = n.get("detail", n.get("details", ""))
        elements_list.append({"data": {"id": nid, "label": label, "detail": detail}})

    for i, e in enumerate(edges):
        elements_list.append(
            {
                "data": {
                    "id": f"e{i}",
                    "source": str(e["from"]),
                    "target": str(e["to"]),
                    "label": e.get("label", ""),
                }
            }
        )

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


def export_html(nodes, edges, task_id_str, **kwargs):
    html_content = generate_html_export(nodes, edges, task_id_str)
    return dcc.send_string(
        html_content,
        filename=make_filename("dag", task_id_str, "html"),
        type="text/html",
    )


# ═══════════════════════════════════════════════════════════════════
# 图形导出（全部委托给 cairoviz_export）
# ═══════════════════════════════════════════════════════════════════
def _safe_graphviz_export(
    fmt: str,
    export_fn: Callable,
    nodes: list,
    edges: list,
    node_states: dict | None,
    task_id_str: str,
    **kwargs,
):
    """统一处理底层渲染的异常捕获和 Dash 封装。

    原因：cairosvg / pydot / graphviz 缺失时，底层会抛 ImportError / ValueError。
    如果不在这里兜住，Dash 前端会直接白屏或报 500。
    """
    try:
        data_bytes = export_fn(
            nodes=nodes,
            edges=edges,
            node_states=node_states,
            title=f"流程图 {task_id_str}",
            **kwargs,
        )
        return dcc.send_bytes(
            data_bytes,
            filename=make_filename("dag", task_id_str, fmt),
        )
    except ImportError as e:
        logger.error("[Export] 缺失依赖: %s", e)
        return dcc.send_string(
            f"导出失败：系统缺失依赖。\n{e}",
            filename="error.txt",
        )
    except Exception as e:
        logger.exception("[Export] 渲染 %s 失败", fmt)
        return dcc.send_string(
            f"导出失败：{type(e).__name__} - {e}",
            filename="error.txt",
        )


def export_dot(nodes, edges, task_id_str, node_states=None, **kwargs):
    """导出 DOT（复用底层引擎，保证样式与 SVG/PNG 一致）"""
    try:
        dot_str = export_dag_to_dot(
            nodes=nodes,
            edges=edges,
            node_states=node_states,
            title=f"流程图 {task_id_str}",
            **kwargs,
        )
        return dcc.send_string(
            dot_str,
            filename=make_filename("dag", task_id_str, "dot"),
            type="text/vnd.graphviz",
        )
    except Exception as e:
        logger.exception("[Export] DOT 渲染失败")
        return dcc.send_string(f"导出 DOT 失败：{e}", filename="error.txt")


def export_svg(nodes, edges, task_id_str, node_states=None, **kwargs):
    """导出 SVG（支持透传 dpi 等参数）"""
    return _safe_graphviz_export(
        "svg", export_dag_to_svg, nodes, edges, node_states, task_id_str, **kwargs
    )


def export_png(nodes, edges, task_id_str, node_states=None, **kwargs):
    """导出 PNG（支持透传 width/height/dpi 等参数）"""
    return _safe_graphviz_export(
        "png", export_dag_to_png, nodes, edges, node_states, task_id_str, **kwargs
    )


# ═══════════════════════════════════════════════════════════════════
# 策略路由（供 Dash 回调调用）
# ═══════════════════════════════════════════════════════════════════
DATA_EXPORT_STRATEGIES = {
    "json": export_json,
    "dot": export_dot,
    "html": export_html,
    "svg": export_svg,
    "png": export_png,
}


__all__ = [
    "DATA_EXPORT_STRATEGIES",
    "generate_html_export",
    "export_json",
    "export_dot",
    "export_html",
    "export_svg",
    "export_png",
]
