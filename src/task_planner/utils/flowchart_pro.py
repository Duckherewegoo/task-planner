"""
flowchart_pro.py — 通用任务流程图渲染器（生产优化版 v7.0）
迁移自原项目，优化：
  - 动态图例（从 STATUS_TEXT 生成）
  - 环检测开关（环境变量 FLOWCHART_CYCLE_DETECTION）
  - PNG/PDF 导出复用代码
  - 更健壮的异常处理
"""
import html
import json
import time
import uuid
import os
from typing import Any, Optional, Tuple, Set, Dict, List

import networkx as nx
from pyvis.network import Network  # type: ignore

from task_planner.infrastructure.config import (
    RENDER_DPI,
    RENDER_EDGE_WIDTH,
    RENDER_FONT,
    RENDER_HEIGHT,
    RENDER_WIDTH,
    STATUS_BORDER,
    STATUS_COLOR,
    STATUS_ICONS,
    STATUS_TEXT,
    EDGE_TYPE_COLOR,
    EDGE_TYPE_HARD,
    EDGE_TYPE_STYLE,
    EDGE_DASH_MAP,
    NODE_STYLES,
    FLOWCHART_CYCLE_DETECTION,  # ← 新增环境变量
)
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.flowchart_pro")

# 兼容旧名
_EDGE_DASH_MAP = EDGE_DASH_MAP


def get_edge_style(edge_type: str) -> dict[str, Any]:
    """根据边类型返回 pyvis add_edge 可用的样式字典。"""
    _default_color = next(iter(EDGE_TYPE_COLOR.values()), "#666666")
    _default_style = next(iter(EDGE_TYPE_STYLE.values()), "solid")
    color = EDGE_TYPE_COLOR.get(edge_type, _default_color)
    style = EDGE_TYPE_STYLE.get(edge_type, _default_style)
    width = RENDER_EDGE_WIDTH * 0.75 if edge_type == "soft" else RENDER_EDGE_WIDTH
    dashes: Any = False if style == "solid" else _EDGE_DASH_MAP.get(style, [10, 5])
    return {"color": color, "width": width, "dashes": dashes}


# ══════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════
def _get_details(node: dict[str, Any]) -> str:
    meta: dict[str, Any] = node.get("meta", {}) or {}
    details = meta.get("details") or node.get("description") or ""
    return str(details).strip()


# ══════════════════════════════════════════════════
#  DAG 全局布局（pyvis set_options 用）
# ══════════════════════════════════════════════════
DAG_LAYOUT: dict[str, Any] = {
    "physics": {
        "enabled": True,
        "hierarchicalRepulsion": {
            "nodeDistance": 220,
            "springLength": 250,
            "springConstant": 0.03,
        },
        "solver": "hierarchicalRepulsion",
    },
    "layout": {
        "hierarchical": {
            "enabled": True,
            "direction": "UD",
            "sortMethod": "directed",
            "levelSeparation": 180,
            "nodeSpacing": 200,
        }
    },
    "nodes": {
        "shape": "box",
        "margin": 14,
        "font": {"size": 14, "face": RENDER_FONT, "align": "center"},
        "borderWidth": 2,
        "shadow": {"enabled": True, "size": 3, "x": 1, "y": 1},
        "widthConstraint": {"maximum": 240},
    },
    "edges": {
        "arrows": {"to": {"enabled": True, "scaleFactor": 0.8}},
        "color": {"color": EDGE_TYPE_COLOR[EDGE_TYPE_HARD], "highlight": "#1a73e8"},
        "smooth": {"type": "cubicBezier", "roundness": 0.4},
        "width": RENDER_EDGE_WIDTH,
    },
    "interaction": {
        "hover": True,
        "tooltipDelay": 80,
        "navigationButtons": True,
        "zoomView": True,
        "dragNodes": True,
    },
}


# ══════════════════════════════════════════════════
#  渲染器主类
# ══════════════════════════════════════════════════
class ProFlowchartRenderer:
    """通用任务流程图渲染器"""

    def __init__(self) -> None:
        try:
            from pyvis.network import Network  # type: ignore  # noqa: F401
        except ImportError as e:
            raise ImportError("请安装 pyvis: pip install pyvis networkx") from e

    def _validate_dag(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], req_id: str
    ) -> nx.DiGraph:
        """校验 DAG，若环检测开启则检测环，否则跳过"""
        g = nx.DiGraph()
        for node in nodes:
            g.add_node(str(node["id"]), **node)
        for edge in edges:
            g.add_edge(str(edge["from"]), str(edge["to"]))

        if FLOWCHART_CYCLE_DETECTION:
            try:
                cycles = list(nx.simple_cycles(g))
                has_cycle = len(cycles) > 0
            except Exception as e:
                logger.warning("[FlowchartPro] 环检测失败: %s (req=%s)", e, req_id)
                has_cycle = False
                cycles = []
            if has_cycle:
                logger.warning(
                    "[FlowchartPro] ⚠️ 检测到 %d 个循环依赖，继续渲染（循环边显示为回边）| req=%s",
                    len(cycles), req_id,
                )
            else:
                logger.info("[FlowchartPro] ✅ DAG 校验通过（无环）| 节点=%d 边=%d req=%s",
                            len(nodes), len(edges), req_id)
        else:
            logger.info("[FlowchartPro] ⏭️ 环检测已禁用 | 节点=%d 边=%d req=%s",
                        len(nodes), len(edges), req_id)
        return g

    def _build_tooltip(self, node: dict[str, Any]) -> str:
        details = _get_details(node)
        if len(details) > 300:
            details = details[:300] + "..."
        escaped = html.escape(details).replace("\n", "<br>")
        meta: dict[str, Any] = node.get("meta", {}) or {}
        pre = meta.get("preconditions", [])
        pre_html = ""
        if pre:
            items = "<br>".join(f"• {html.escape(str(p))}" for p in pre[:2])
            pre_html = f"<br><b>前置条件:</b><br>{items}"
        name = str(node.get("name", f"步骤{node.get('id')}"))
        return f"<b>{html.escape(name)}</b><br><br>{escaped}{pre_html}"

    def _build_card_data(self, nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        cards: dict[str, dict[str, Any]] = {}
        for node in nodes:
            nid = str(node["id"])
            details = _get_details(node)
            meta: dict[str, Any] = node.get("meta", {}) or {}
            status_code = int(node.get("status", 0))
            cards[nid] = {
                "id": nid,
                "name": html.escape(str(node.get("name", f"步骤{node['id']}"))),
                "status": status_code,
                "status_text": html.escape(STATUS_TEXT.get(status_code, "未知")),
                "details": html.escape(details).replace("\n", "<br>"),
                "preconditions": [html.escape(str(p)) for p in meta.get("preconditions", [])],
                "postconditions": [html.escape(str(p)) for p in meta.get("postconditions", [])],
                "retry_policy": html.escape(str(meta.get("retry_policy", "失败后重试，最多3次"))),
                "retry_count": int(node.get("retry_count", 0)),
            }
        return cards

    def _build_network(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        req_id: str,
        cycle_edges: Optional[Set[Tuple[str, str]]] = None,
    ) -> Network:
        if cycle_edges is None:
            cycle_edges = set()

        net = Network(
            height="580px",
            width="100%",
            bgcolor="#f8fafc",
            font_color="#1e293b",
            directed=True,
            notebook=False,
            cdn_resources="in_line",
        )
        net.set_options(json.dumps(DAG_LAYOUT))

        # 添加节点
        for node in nodes:
            nid = str(node["id"])
            status = int(node.get("status", 0))
            style = NODE_STYLES.get(status, NODE_STYLES.get(0, {}))
            name = str(node.get("name", f"步骤{node['id']}"))
            label = f"{style.get('icon', '⏳')} {name}"
            net.add_node(
                nid,
                label=label,
                title=self._build_tooltip(node),
                color={
                    "background": style.get("bg", "#ffffff"),
                    "border": style.get("border", "#e5e7eb"),
                    "highlight": {"background": "#fef3c7", "border": "#f59e0b"},
                },
                borderWidth=2,
                shape="box",
                borderDashes=(status == 3),
            )

        # 添加边
        for edge in edges:
            src = str(edge["from"])
            tgt = str(edge["to"])
            is_cycle = (src, tgt) in cycle_edges or src == tgt
            edge_label = str(edge.get("label", ""))

            if is_cycle:
                cycle_color = EDGE_TYPE_COLOR.get("retry", "#f59e0b")
                net.add_edge(
                    src, tgt,
                    label=edge_label,
                    title=edge_label,
                    color={"color": cycle_color},
                    width=2,
                    dashes=[8, 4],
                    smooth={"type": "curvedCW", "roundness": 0.25},
                    font={"size": 11, "face": RENDER_FONT, "align": "middle", "color": "#92400e"},
                )
            else:
                edge_type = str(edge.get("type", EDGE_TYPE_HARD)).lower()
                style = get_edge_style(edge_type)
                net.add_edge(
                    src, tgt,
                    label=edge_label,
                    title=edge_label,
                    color={"color": style["color"]},
                    width=style["width"],
                    dashes=style["dashes"],
                    font={"size": 11, "face": RENDER_FONT, "align": "middle"},
                )

        return net

    # ──────────────────────────────────────────────
    #  动态图例（优化点）
    # ──────────────────────────────────────────────
    def _build_legend(self, uid: str) -> str:
        """从 STATUS_TEXT / STATUS_BORDER 动态生成图例"""
        # 过滤出有边框颜色的状态
        items = []
        for code, text in STATUS_TEXT.items():
            if code in STATUS_BORDER:
                color = STATUS_BORDER[code]
                items.append(
                    f'<span style="color:{color};">●</span> {html.escape(text)}'
                )
        # 每两个一组
        rows = []
        for i in range(0, len(items), 2):
            rows.append(" &nbsp; ".join(items[i:i+2]))
        legend_html = (
            f'<div id="legend-{uid}" style="'
            f'position:absolute;top:14px;right:18px;z-index:10;'
            f'background:white;border:1px solid #e2e8f0;border-radius:10px;'
            f'padding:10px 14px;font-size:12px;'
            f'box-shadow:0 2px 8px rgba(0,0,0,0.06);">'
            f'<b style="font-size:13px;color:#1e293b;">任务状态图例</b><br>'
            f'{"<br>".join(rows)}'
            f'</div>'
        )
        return legend_html

    # ──────────────────────────────────────────────
    #  交互式 HTML 渲染（主入口）
    # ──────────────────────────────────────────────
    def render_interactive(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        task_id: str,
        req_id: str,
    ) -> str:
        g = self._validate_dag(nodes, edges, req_id)

        # 提取循环边集合
        cycle_edges: Set[Tuple[str, str]] = set()
        if FLOWCHART_CYCLE_DETECTION:
            try:
                for cycle in nx.simple_cycles(g):
                    for i in range(len(cycle)):
                        cycle_edges.add((cycle[i], cycle[(i + 1) % len(cycle)]))
            except Exception as e:
                logger.warning("[FlowchartPro] 环边提取失败: %s (req=%s)", e, req_id)

        net = self._build_network(nodes, edges, req_id, cycle_edges=cycle_edges)
        base_html = net.generate_html()

        cards = self._build_card_data(nodes)
        uid = uuid.uuid4().hex[:8]
        data_script = (
            f'<script type="application/json" id="flowchart-data-{uid}">'
            f"{json.dumps(cards, ensure_ascii=False)}"
            f"</script>"
        )
        legend = self._build_legend(uid)

        if "</body>" in base_html:
            base_html = base_html.replace("</body>", data_script + legend + "</body>")
        else:
            base_html = base_html + data_script + legend

        logger.info("[FlowchartPro] ✅ 流程图渲染完成 | task=%s req=%s", task_id, req_id)
        return base_html

    # ──────────────────────────────────────────────
    #  SVG 提取
    # ──────────────────────────────────────────────
    def _extract_svg_from_html(self, html_content: str) -> str:
        import re
        from html import unescape

        match = re.search(r"(<svg[^>]*>.*?</svg>)", html_content, re.DOTALL)
        if not match:
            return f'<div style="width:{RENDER_WIDTH}px;height:{RENDER_HEIGHT}px;">{html_content}</div>'

        svg_unescaped = unescape(match.group(1))

        try:
            import lxml.etree as ET  # type: ignore
            parser = ET.XMLParser(resolve_entities=True, recover=True, encoding="utf-8")
            root = ET.fromstring(svg_unescaped.encode("utf-8"), parser)
            return ET.tostring(root, encoding="unicode", method="xml")
        except ImportError:
            logger.debug("[FlowchartPro] lxml 未安装，使用标准库降级")
        except (OSError, RuntimeError) as e:
            logger.warning("[FlowchartPro] lxml SVG 清理失败: %s", e)

        # 降级：用 html.parser 手动提取
        try:
            from html.parser import HTMLParser

            class _SVGExtractor(HTMLParser):
                def __init__(self) -> None:
                    super().__init__()
                    self.parts: list[str] = []
                    self.depth = 0

                def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                    if tag == "svg":
                        self.depth += 1
                        attrs_str = " ".join(f'{k}="{v}"' for k, v in attrs)
                        self.parts.append(f"<svg {attrs_str}>")
                    elif self.depth > 0:
                        attrs_str = " ".join(f'{k}="{v}"' for k, v in attrs)
                        self.parts.append(f"<{tag} {attrs_str}>")

                def handle_endtag(self, tag: str) -> None:
                    if tag == "svg" and self.depth > 0:
                        self.parts.append("</svg>")
                        self.depth -= 1
                    elif self.depth > 0:
                        self.parts.append(f"</{tag}>")

                def handle_data(self, data: str) -> None:
                    if self.depth > 0:
                        self.parts.append(data)

            parser = _SVGExtractor()
            parser.feed(svg_unescaped)
            if parser.parts:
                return "".join(parser.parts)
        except (OSError, RuntimeError) as e:
            logger.warning("[FlowchartPro] html.parser 清理失败: %s", e)

        return svg_unescaped

    # ──────────────────────────────────────────────
    #  PNG/PDF 导出复用（优化点）
    # ──────────────────────────────────────────────
    def _render_png_pdf(self, svg_content: str, fmt: str) -> str:
        """内部方法：将 SVG 转为 PNG 或 PDF（Base64）"""
        try:
            import cairosvg  # type: ignore
            if fmt == "png":
                output = cairosvg.svg2png(
                    bytestring=svg_content.encode("utf-8"),
                    dpi=RENDER_DPI,
                    output_width=RENDER_WIDTH,
                    output_height=RENDER_HEIGHT,
                )
                mime = "image/png"
            else:  # pdf
                output = cairosvg.svg2pdf(
                    bytestring=svg_content.encode("utf-8"),
                    dpi=RENDER_DPI,
                )
                mime = "application/pdf"

            import base64
            b64 = base64.b64encode(output).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except ImportError:
            raise ValueError(f"导出 {fmt.upper()} 需要安装 cairosvg: pip install cairosvg")
        except (OSError, RuntimeError, ValueError) as e:
            raise ValueError(f"{fmt.upper()} 导出失败: {e}")

    # ──────────────────────────────────────────────
    #  错误页面
    # ──────────────────────────────────────────────
    def _error_html(self, message: str, req_id: str) -> str:
        return (
            '<div style="width:100%;height:520px;display:flex;'
            'align-items:center;justify-content:center;'
            'border:1px solid #fecaca;border-radius:12px;'
            'background:#fef2f2;padding:24px;">'
            '<div style="color:#dc2626;text-align:center;'
            'max-width:600px;">'
            '<h3 style="margin:0 0 12px;font-size:20px;">'
            '⚠️ 流程图生成失败</h3>'
            f'<p style="margin:0 0 8px;color:#991b1b;line-height:1.6;">'
            f"{html.escape(message)}</p>"
            f'<p style="margin:0;color:#6b7280;font-size:12px;">'
            f"请求ID: {html.escape(req_id)}</p>"
            "</div></div>"
        )


# ══════════════════════════════════════════════════
#  全局单例 + 兼容接口
# ══════════════════════════════════════════════════
flowchart_pro = ProFlowchartRenderer()


def render_for_gradio(nodes, edges, task_id, req_id):
    html = flowchart_pro.render_interactive(nodes, edges, task_id, req_id)
    return f'<div class="pyvis-container" data-task="{task_id}">{html}</div>'


def build_clean_svg(nodes, edges, task_id):
    # （原样保留，未变）
    width, height = 1200, 800
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<style>'
        '.node-rect { fill: #4A90D9; rx: 8; ry: 8; }'
        '.node-text { fill: white; font: 14px sans-serif; text-anchor: middle; dominant-baseline: middle; }'
        '.edge { fill: none; stroke: #666; stroke-width: 2; marker-end: url(#arrowhead); }'
        '</style>',
        '<defs>'
        '<marker id="arrowhead" markerWidth="10" markerHeight="7" '
        'refX="10" refY="3.5" orient="auto">'
        '<polygon points="0 0, 10 3.5, 0 7" fill="#666" />'
        '</marker>'
        '</defs>',
    ]
    cols, rows = 4, (len(nodes) + 3) // 4
    cell_w, cell_h = width // cols, height // rows
    node_positions = {}
    for i, node in enumerate(nodes):
        col, row = i % cols, i // cols
        x = col * cell_w + cell_w // 2
        y = row * cell_h + cell_h // 2
        node_positions[node["id"]] = (x, y)
        safe_label = html.escape(str(node.get("label", node["id"])), quote=True)
        svg_parts.append(
            f'<rect class="node-rect" x="{x-80}" y="{y-25}" width="160" height="50"/>'
        )
        svg_parts.append(f'<text class="node-text" x="{x}" y="{y}">{safe_label}</text>')
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in node_positions and tgt in node_positions:
            x1, y1 = node_positions[src]
            x2, y2 = node_positions[tgt]
            svg_parts.append(f'<path class="edge" d="M{x1+80} {y1} L{x2-80} {y2}" />')
    svg_parts.append('</svg>')
    return '\n'.join(svg_parts)


def export(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    task_id: str,
    fmt: str,
    req_id: str,
) -> str:
    fmt = fmt.lower()
    logger.info("[FlowchartPro] 导出 | task=%s fmt=%s req=%s", task_id, fmt, req_id)

    if fmt == "html":
        return flowchart_pro.render_interactive(nodes, edges, task_id, req_id)
    if fmt == "json":
        return json.dumps(
            {
                "task_id": task_id,
                "nodes": nodes,
                "edges": edges,
                "export_time": int(time.time()),
            },
            ensure_ascii=False,
            indent=2,
        )

    html_content = flowchart_pro.render_interactive(nodes, edges, task_id, req_id)
    svg_content = flowchart_pro._extract_svg_from_html(html_content)

    if fmt == "svg":
        return svg_content
    if fmt in ("png", "pdf"):
        return flowchart_pro._render_png_pdf(svg_content, fmt)
    if fmt == "dot":
        try:
            import pydot  # type: ignore
            g = nx.DiGraph()
            for n in nodes:
                g.add_node(n["id"], label=str(n.get("name", f"步骤{n['id']}")))
            for e in edges:
                g.add_edge(e["from"], e["to"], label=str(e.get("label", "")))
            dot_data = nx.nx_pydot.to_pydot(g)
            return dot_data.to_string()
        except ImportError:
            raise ValueError("导出 DOT 需要安装 pydot: pip install pydot")
        except (ImportError, OSError, RuntimeError) as e:
            raise ValueError(f"DOT 导出失败: {e}")

    raise ValueError(f"不支持的导出格式: {fmt}（支持 svg / png / pdf / dot / html / json）")
