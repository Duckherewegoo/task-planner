"""export.py — 数据/图片导出（JSON/DOT/HTML/SVG/PNG）"""
from __future__ import annotations

import json
import textwrap

from dash import dcc

from task_planner.infrastructure.logger_setup import get_logger
from task_planner.utils.pyvis_export import export_dag_to_png, export_dag_to_svg

from .data_ops import make_filename

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  DOT / JSON / HTML
# ═══════════════════════════════════════════════════════════════════
def _escape_dot_label(text: str) -> str:
    if not text:
        return ""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def export_json(nodes, edges, task_id_str):
    content = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2)
    return dcc.send_string(
        content,
        filename=make_filename("dag", task_id_str, "json"),
        type="application/json",
    )


def export_dot(nodes, edges, task_id_str):
    lines = [
        "digraph G {",
        "  rankdir=TB;",
        '  node [shape=box, style="rounded,filled", fillcolor="#E8F4FD"];',
    ]
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
        filename=make_filename("dag", task_id_str, "dot"),
        type="text/vnd.graphviz",
    )


def generate_html_export(nodes, edges, task_id) -> str:
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


def export_html(nodes, edges, task_id_str):
    html_content = generate_html_export(nodes, edges, task_id_str)
    return dcc.send_string(
        html_content,
        filename=make_filename("dag", task_id_str, "html"),
        type="text/html",
    )


DATA_EXPORT_STRATEGIES = {
    "json": export_json,
    "dot":  export_dot,
    "html": export_html,
}


# ═══════════════════════════════════════════════════════════════════
#  SVG / PNG
# ═══════════════════════════════════════════════════════════════════
def export_svg_bytes(nodes, edges, node_states, title):
    return export_dag_to_svg(
        nodes=nodes, edges=edges,
        output_path=None,
        node_states=node_states,
        title=title, dpi=200,
    )


def export_png_bytes(nodes, edges, node_states, title):
    return export_dag_to_png(
        nodes=nodes, edges=edges,
        output_path=None,
        node_states=node_states,
        title=title, width=1600, height=1200, dpi=200,
    )


__all__ = [
    "DATA_EXPORT_STRATEGIES",
    "export_svg_bytes",
    "export_png_bytes",
    "generate_html_export",
]
