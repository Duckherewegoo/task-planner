"""
flowchart_pro.py — 通用任务流程图渲染器（生产优化版 v8）

Changelog:
  ── v7.1 ──
  ✅ P0-1：_get_details 修复字段优先级（node.details > meta.details > description）
  ✅ P0-2：build_clean_svg 兼容 edge 的 from/to 与 source/target 双契约
  ✅ P1-1：simple_cycles 只跑一次，cycle_edges 从 _validate_dag 返回
  ✅ P1-2：render_interactive 加 try/except，失败降级到 _error_html
  ✅ P1-3：删除 __init__ 里的死 try/except
  ✅ P1-4：export 的 dot 分支 except ImportError 冗余去除
  ✅ P2-x：移除 render_for_gradio；预编译 DAG_LAYOUT JSON；
           属性值 html.escape；import re 提到顶部

  ── v8（KISS 加固，不影响功能）──
  ✅ P1-5：加本地 _safe_int，替换所有 int(node.get(...))，
           容忍 None / 非法字符串。
  ✅ P1-6：build_clean_svg 里 node["id"] 改 node.get("id", "?")，
           避免 KeyError。
  ✅ P1-7：_extract_svg_from_html 的 lxml 分支加 except Exception，
           捕获 XMLSyntaxError 等 lxml 特有异常。
  ✅ P1-8：render_interactive 用 rsplit 精确替换**最后一个** </body>，
           避免多 </body> 场景污染结构。
  ✅ P1-9：_validate_dag 的 nx.simple_cycles 加上限 _MAX_CYCLES_DETECT=50，
           超过后只记录数量不展开。
  ✅ P2-7：_extract_svg_from_html 用 html.unescape（顶部已 import html）。
  ✅ P2-8：_render_png_pdf 的 import base64 提到模块顶部。
  ✅ P2-9：get_edge_style 的 "soft" 改用 EDGE_TYPE_SOFT 常量。
  ✅ P2-10：__init__ 注释与代码对齐（去掉"死 try/except"措辞）。
  ✅ P2-11：_build_tooltip / _build_card_data 的 node['id'] 改 .get。
"""
from __future__ import annotations

import base64
import html
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
from pyvis.network import Network  # type: ignore

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import (
    EDGE_DASH_MAP,
    EDGE_TYPE_COLOR,
    EDGE_TYPE_HARD,
    EDGE_TYPE_SOFT,
    EDGE_TYPE_STYLE,
    NODE_STYLES,
    STATUS_BORDER,
    STATUS_COLOR,   # noqa: F401  保留以兼容外部 import
    STATUS_ICONS,
    STATUS_TEXT,
)
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.flowchart_pro")

# ── 模块级配置快照（渲染参数改动需重启，这是有意为之） ──
FLOWCHART_CYCLE_DETECTION = _hub.dev.FLOWCHART_CYCLE_DETECTION
RENDER_DPI = _hub.dev.RENDER_DPI
RENDER_EDGE_WIDTH = _hub.dev.RENDER_EDGE_WIDTH
RENDER_FONT = _hub.dev.RENDER_FONT
RENDER_HEIGHT = _hub.dev.RENDER_HEIGHT
RENDER_WIDTH = _hub.dev.RENDER_WIDTH

_EDGE_DASH_MAP = EDGE_DASH_MAP

# ✅ P1-9：环检测上限，畸形图不再无脑展开
_MAX_CYCLES_DETECT = 50


# ══════════════════════════════════════════════════
#  通用工具
# ══════════════════════════════════════════════════
def _safe_int(val: Any, default: int = 0) -> int:
    """✅ P1-5：容忍 None / 字符串 / 非法值"""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════
#  边样式
# ══════════════════════════════════════════════════
def get_edge_style(edge_type: str) -> Dict[str, Any]:
    """根据边类型返回 pyvis add_edge 可用的样式字典。"""
    color = EDGE_TYPE_COLOR.get(edge_type, "#666666")
    style = EDGE_TYPE_STYLE.get(edge_type, "solid")
    # ✅ P2-9：用常量替代硬编码 "soft"
    width = (
        RENDER_EDGE_WIDTH * 0.75
        if edge_type == EDGE_TYPE_SOFT
        else RENDER_EDGE_WIDTH
    )
    dashes: Any = (
        False if style == "solid" else _EDGE_DASH_MAP.get(style, [10, 5])
    )
    return {"color": color, "width": width, "dashes": dashes}


# ══════════════════════════════════════════════════
#  节点详情提取
# ══════════════════════════════════════════════════
def _get_details(node: Dict[str, Any]) -> str:
    """
    节点详情提取（优先级）：
      node.details > meta.details > node.description
    """
    meta: Dict[str, Any] = node.get("meta", {}) or {}
    details = (
        node.get("details")
        or meta.get("details")
        or node.get("description")
        or ""
    )
    return str(details).strip()


# ══════════════════════════════════════════════════
#  DAG 布局
# ══════════════════════════════════════════════════
DAG_LAYOUT: Dict[str, Any] = {
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
        "color": {
            "color": EDGE_TYPE_COLOR[EDGE_TYPE_HARD],
            "highlight": "#1a73e8",
        },
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

_DAG_LAYOUT_JSON = json.dumps(DAG_LAYOUT)


# ══════════════════════════════════════════════════
#  渲染器
# ══════════════════════════════════════════════════
class ProFlowchartRenderer:
    """通用任务流程图渲染器"""

    def __init__(self) -> None:
        # ✅ P2-10：占位，为未来懒加载预留（pyvis 当前在模块顶部 import）
        pass

    # ── DAG 校验 + 环边提取 ──
    def _validate_dag(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        req_id: str,
    ) -> Tuple[nx.DiGraph, Set[Tuple[str, str]]]:
        g = nx.DiGraph()
        for node in nodes:
            g.add_node(str(node.get("id", "")), **node)
        for edge in edges:
            src = edge.get("from")
            tgt = edge.get("to")
            if src is None or tgt is None:
                continue
            g.add_edge(str(src), str(tgt))

        cycle_edges: Set[Tuple[str, str]] = set()

        if not FLOWCHART_CYCLE_DETECTION:
            logger.info(
                "[FlowchartPro] ⏭️ 环检测已禁用 | 节点=%d 边=%d req=%s",
                len(nodes), len(edges), req_id,
            )
            return g, cycle_edges

        try:
            # ✅ P1-9：限制展开上限，畸形图不卡死
            cycles_iter = nx.simple_cycles(g)
            cycles: List[List[str]] = []
            for i, c in enumerate(cycles_iter):
                if i >= _MAX_CYCLES_DETECT:
                    logger.warning(
                        "[FlowchartPro] ⚠️ 环数量超过 %d，仅记录前 %d 个 | req=%s",
                        _MAX_CYCLES_DETECT, _MAX_CYCLES_DETECT, req_id,
                    )
                    break
                cycles.append(c)

            if cycles:
                logger.warning(
                    "[FlowchartPro] ⚠️ 检测到 %d 个循环依赖，继续渲染 | req=%s",
                    len(cycles), req_id,
                )
                for cycle in cycles:
                    for i in range(len(cycle)):
                        cycle_edges.add(
                            (cycle[i], cycle[(i + 1) % len(cycle)])
                        )
            else:
                logger.info(
                    "[FlowchartPro] ✅ DAG 校验通过（无环）| 节点=%d 边=%d req=%s",
                    len(nodes), len(edges), req_id,
                )
        except Exception as e:
            logger.warning(
                "[FlowchartPro] 环检测失败: %s (req=%s)", e, req_id,
            )

        return g, cycle_edges

    # ── Tooltip ──
    def _build_tooltip(self, node: Dict[str, Any]) -> str:
        details = _get_details(node)
        if len(details) > 300:
            details = details[:300] + "..."
        escaped = html.escape(details).replace("\n", "<br>")
        meta: Dict[str, Any] = node.get("meta", {}) or {}
        pre = meta.get("preconditions", [])
        pre_html = ""
        if pre:
            items = "<br>".join(f"• {html.escape(str(p))}" for p in pre[:2])
            pre_html = f"<br><b>前置条件:</b><br>{items}"
        # ✅ P2-11：用 .get 避免 KeyError
        name = str(node.get("name", f"步骤{node.get('id', '?')}"))
        return f"<b>{html.escape(name)}</b><br><br>{escaped}{pre_html}"

    # ── 卡片数据 ──
    def _build_card_data(
        self, nodes: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        cards: Dict[str, Dict[str, Any]] = {}
        for node in nodes:
            nid = str(node.get("id", ""))
            if not nid:
                continue
            details = _get_details(node)
            meta: Dict[str, Any] = node.get("meta", {}) or {}
            status_code = _safe_int(node.get("status"), default=0)
            cards[nid] = {
                "id": nid,
                "name": html.escape(
                    str(node.get("name", f"步骤{nid}"))
                ),
                "status": status_code,
                "status_text": html.escape(
                    STATUS_TEXT.get(status_code, "未知")
                ),
                "details": html.escape(details).replace("\n", "<br>"),
                "preconditions": [
                    html.escape(str(p))
                    for p in meta.get("preconditions", [])
                ],
                "postconditions": [
                    html.escape(str(p))
                    for p in meta.get("postconditions", [])
                ],
                "retry_policy": html.escape(
                    str(meta.get("retry_policy", "失败后重试，最多3次"))
                ),
                "retry_count": _safe_int(node.get("retry_count"), default=0),
            }
        return cards

    # ── 网络构建 ──
    def _build_network(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
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
        net.set_options(_DAG_LAYOUT_JSON)

        # 节点
        for node in nodes:
            nid = str(node.get("id", ""))
            if not nid:
                continue
            status = _safe_int(node.get("status"), default=0)
            style = NODE_STYLES.get(status, NODE_STYLES.get(0, {}))
            name = str(node.get("name", f"步骤{nid}"))
            label = f"{style.get('icon', '⏳')} {name}"
            net.add_node(
                nid,
                label=label,
                title=self._build_tooltip(node),
                color={
                    "background": style.get("bg", "#ffffff"),
                    "border": style.get("border", "#e5e7eb"),
                    "highlight": {
                        "background": "#fef3c7",
                        "border": "#f59e0b",
                    },
                },
                borderWidth=2,
                shape="box",
                borderDashes=(status == 3),
            )

        # 边
        for edge in edges:
            src = edge.get("from")
            tgt = edge.get("to")
            if src is None or tgt is None:
                continue
            src = str(src)
            tgt = str(tgt)
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
                    font={
                        "size": 11, "face": RENDER_FONT,
                        "align": "middle", "color": "#92400e",
                    },
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
                    font={
                        "size": 11, "face": RENDER_FONT, "align": "middle",
                    },
                )

        return net

    # ── 图例 ──
    def _build_legend(self, uid: str) -> str:
        items = []
        for code, text in STATUS_TEXT.items():
            if code in STATUS_BORDER:
                color = STATUS_BORDER[code]
                items.append(
                    f'<span style="color:{color};">●</span> '
                    f"{html.escape(text)}"
                )
        rows = []
        for i in range(0, len(items), 2):
            rows.append(" &nbsp; ".join(items[i:i + 2]))
        return (
            f'<div id="legend-{uid}" style="'
            f'position:absolute;top:14px;right:18px;z-index:10;'
            f'background:white;border:1px solid #e2e8f0;border-radius:10px;'
            f'padding:10px 14px;font-size:12px;'
            f'box-shadow:0 2px 8px rgba(0,0,0,0.06);">'
            f'<b style="font-size:13px;color:#1e293b;">任务状态图例</b><br>'
            f'{"<br>".join(rows)}'
            f'</div>'
        )

    # ── 交互式渲染（主入口） ──
    def render_interactive(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        task_id: str,
        req_id: str,
    ) -> str:
        try:
            _g, cycle_edges = self._validate_dag(nodes, edges, req_id)

            net = self._build_network(
                nodes, edges, req_id, cycle_edges=cycle_edges,
            )
            base_html = net.generate_html()

            cards = self._build_card_data(nodes)
            uid = uuid.uuid4().hex[:8]
            data_script = (
                f'<script type="application/json" id="flowchart-data-{uid}">'
                f"{json.dumps(cards, ensure_ascii=False)}"
                f"</script>"
            )
            legend = self._build_legend(uid)

            # ✅ P1-8：精确替换**最后一个** </body>，避免多 </body> 污染
            if "</body>" in base_html:
                head, _, tail = base_html.rpartition("</body>")
                base_html = head + data_script + legend + "</body>" + tail
            else:
                base_html = base_html + data_script + legend

            logger.info(
                "[FlowchartPro] ✅ 流程图渲染完成 | task=%s req=%s",
                task_id, req_id,
            )
            return base_html

        except Exception as e:
            logger.exception(
                "[FlowchartPro] 渲染失败: %s (req=%s)", e, req_id,
            )
            return self._error_html(f"{type(e).__name__}: {e}", req_id)

    # ── SVG 提取 ──
    def _extract_svg_from_html(self, html_content: str) -> str:
        match = re.search(r"(<svg[^>]*>.*?</svg>)", html_content, re.DOTALL)
        if not match:
            return (
                f'<div style="width:{RENDER_WIDTH}px;'
                f'height:{RENDER_HEIGHT}px;">'
                f"{html_content}</div>"
            )

        # ✅ P2-7：用 html.unescape（顶部已 import html）
        svg_unescaped = html.unescape(match.group(1))

        # 优先用 lxml
        try:
            import lxml.etree as ET  # type: ignore
            parser = ET.XMLParser(
                resolve_entities=True, recover=True, encoding="utf-8",
            )
            root = ET.fromstring(svg_unescaped.encode("utf-8"), parser)
            return ET.tostring(root, encoding="unicode", method="xml")
        except ImportError:
            logger.debug("[FlowchartPro] lxml 未安装，使用标准库降级")
        except Exception as e:
            # ✅ P1-7：捕获所有异常（含 lxml.XMLSyntaxError 等）
            logger.warning("[FlowchartPro] lxml SVG 清理失败: %s", e)

        # 降级：html.parser
        try:
            from html.parser import HTMLParser

            class _SVGExtractor(HTMLParser):
                def __init__(self) -> None:
                    super().__init__()
                    self.parts: List[str] = []
                    self.depth = 0

                def _attrs_str(self, attrs) -> str:
                    return " ".join(
                        f'{k}="{html.escape(str(v), quote=True)}"'
                        for k, v in attrs
                    )

                def handle_starttag(self, tag, attrs) -> None:
                    if tag == "svg":
                        self.depth += 1
                        self.parts.append(f"<svg {self._attrs_str(attrs)}>")
                    elif self.depth > 0:
                        self.parts.append(f"<{tag} {self._attrs_str(attrs)}>")

                def handle_startendtag(self, tag, attrs) -> None:
                    if self.depth > 0:
                        self.parts.append(
                            f"<{tag} {self._attrs_str(attrs)}/>"
                        )

                def handle_endtag(self, tag) -> None:
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
        except Exception as e:
            logger.warning("[FlowchartPro] html.parser 清理失败: %s", e)

        return svg_unescaped

    # ── PNG / PDF ──
    def _render_png_pdf(self, svg_content: str, fmt: str) -> str:
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

            # ✅ P2-8：base64 已在顶部 import
            b64 = base64.b64encode(output).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except ImportError:
            raise ValueError(
                f"导出 {fmt.upper()} 需要安装 cairosvg: pip install cairosvg"
            )
        except (OSError, RuntimeError, ValueError) as e:
            raise ValueError(f"{fmt.upper()} 导出失败: {e}")

    # ── 错误页 ──
    def _error_html(self, message: str, req_id: str) -> str:
        return (
            '<div style="width:100%;height:520px;display:flex;'
            'align-items:center;justify-content:center;'
            'border:1px solid #fecaca;border-radius:12px;'
            'background:#fef2f2;padding:24px;">'
            '<div style="color:#dc2626;text-align:center;max-width:600px;">'
            '<h3 style="margin:0 0 12px;font-size:20px;">'
            "⚠️ 流程图生成失败</h3>"
            '<p style="margin:0 0 8px;color:#991b1b;line-height:1.6;">'
            f"{html.escape(message)}</p>"
            '<p style="margin:0;color:#6b7280;font-size:12px;">'
            f"请求ID: {html.escape(req_id)}</p>"
            "</div></div>"
        )


# ══════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════
flowchart_pro = ProFlowchartRenderer()


# ══════════════════════════════════════════════════
#  兼容接口
# ══════════════════════════════════════════════════
def build_clean_svg(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    task_id: str,
) -> str:
    """极简 SVG 渲染（用于静态导出）"""
    width, height = 1200, 800
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        "<style>"
        ".node-rect { fill: #4A90D9; rx: 8; ry: 8; }"
        ".node-text { fill: white; font: 14px sans-serif; "
        "text-anchor: middle; dominant-baseline: middle; }"
        ".edge { fill: none; stroke: #666; stroke-width: 2; "
        "marker-end: url(#arrowhead); }"
        "</style>",
        "<defs>"
        '<marker id="arrowhead" markerWidth="10" markerHeight="7" '
        'refX="10" refY="3.5" orient="auto">'
        '<polygon points="0 0, 10 3.5, 0 7" fill="#666" />'
        "</marker>"
        "</defs>",
    ]
    cols = 4
    rows = (len(nodes) + cols - 1) // cols or 1
    cell_w, cell_h = width // cols, height // rows
    node_positions: Dict[Any, Tuple[int, int]] = {}

    for i, node in enumerate(nodes):
        # ✅ P1-6：用 .get 避免 KeyError
        nid = node.get("id", f"__node_{i}")
        col, row = i % cols, i // cols
        x = col * cell_w + cell_w // 2
        y = row * cell_h + cell_h // 2
        node_positions[nid] = (x, y)
        safe_label = html.escape(
            str(node.get("label", nid)), quote=True
        )
        svg_parts.append(
            f'<rect class="node-rect" x="{x - 80}" y="{y - 25}" '
            f'width="160" height="50"/>'
        )
        svg_parts.append(
            f'<text class="node-text" x="{x}" y="{y}">{safe_label}</text>'
        )

    for edge in edges:
        # 兼容 from/to 与 source/target
        src = edge.get("from") if "from" in edge else edge.get("source")
        tgt = edge.get("to") if "to" in edge else edge.get("target")

        if src is None or tgt is None:
            logger.warning(
                "[FlowchartPro] build_clean_svg: edge 缺少端点: %s", edge,
            )
            continue

        if src in node_positions and tgt in node_positions:
            x1, y1 = node_positions[src]
            x2, y2 = node_positions[tgt]
            svg_parts.append(
                f'<path class="edge" d="M{x1 + 80} {y1} L{x2 - 80} {y2}" />'
            )

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def export(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    task_id: str,
    fmt: str,
    req_id: str,
) -> str:
    """导出为指定格式"""
    fmt = fmt.lower()
    logger.info(
        "[FlowchartPro] 导出 | task=%s fmt=%s req=%s", task_id, fmt, req_id,
    )

    if fmt == "html":
        return flowchart_pro.render_interactive(
            nodes, edges, task_id, req_id
        )

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

    html_content = flowchart_pro.render_interactive(
        nodes, edges, task_id, req_id
    )
    svg_content = flowchart_pro._extract_svg_from_html(html_content)

    if fmt == "svg":
        return svg_content

    if fmt in ("png", "pdf"):
        return flowchart_pro._render_png_pdf(svg_content, fmt)

    if fmt == "dot":
        try:
            import pydot  # type: ignore  # noqa: F401

            g = nx.DiGraph()
            for n in nodes:
                nid = n.get("id")
                if nid is None:
                    continue
                g.add_node(
                    nid, label=str(n.get("name", f"步骤{nid}"))
                )
            for e in edges:
                src = e.get("from")
                tgt = e.get("to")
                if src is None or tgt is None:
                    continue
                g.add_edge(src, tgt, label=str(e.get("label", "")))
            dot_data = nx.nx_pydot.to_pydot(g)
            return dot_data.to_string()
        except ImportError:
            raise ValueError("导出 DOT 需要安装 pydot: pip install pydot")
        except (OSError, RuntimeError) as e:
            raise ValueError(f"DOT 导出失败: {e}")

    raise ValueError(
        f"不支持的导出格式: {fmt}（支持 svg / png / pdf / dot / html / json）"
    )


__all__ = [
    "ProFlowchartRenderer",
    "flowchart_pro",
    "get_edge_style",
    "build_clean_svg",
    "export",
    "DAG_LAYOUT",
]
