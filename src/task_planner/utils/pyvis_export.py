"""
pyvis_export.py — DAG 静态可视化导出引擎 (Graphviz Backend) v4
═══════════════════════════════════════════════════════════════════════
纯后端静态渲染，基于 Graphviz/pydot + cairosvg。
输出 SVG/PNG/PDF/DOT 字节流或文件，适用于报告、邮件、PPT、CI。

与 flowchart_pro.py 的关系：
  - flowchart_pro: 交互式 HTML (PyVis + vis.js)
  - pyvis_export:  静态导出 (Graphviz + cairosvg)

调用方式：所有导出函数均为同步，请用 asyncio.to_thread 隔离阻塞 I/O。

Changelog:
  ── v3.0 ──
  ✅ P0-1：export_from_dag_store 的 dot 分支不再传递 output_path
  ✅ P0-2：边样式按 edge["type"] 区分
  ✅ P1-1：export_dag_to_png 的 dpi 参数真正生效
  ✅ P1-2：export_dag_to_svg 默认 dpi 从 800 改为 96
  ✅ P1-3：节点 label 截断改用 wcswidth 显示宽度
  ✅ P1-4：title 注入 DOT 前用 _escape_dot_text 转义
  ✅ P2-1：删除未使用的 config 导入
  ✅ P2-3：跳过无 ID 节点时打印节点内容

  ── v4（KISS 加固）──
  ✅ P1-5：顶部 logger import 改为直接导入，去掉 try/except 反模式。
           内部模块不可用时应当立即暴露问题，而非静默降级。
  ✅ P1-6：_shorten_label 改累积宽度，O(n²) → O(n)。
  ✅ P1-7：4 个 export 函数去掉"整体 try/except 打日志再重抛"，
           改为只在核心渲染步骤捕获，异常栈更清晰。
  ✅ P2-4：删除文件末尾废弃的 _render_pyvis_html 注释块。
  ✅ P2-5：加 __all__，明确公开接口。
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)

from wcwidth import wcswidth

from task_planner.infrastructure.constants import (
    DEFAULT_EDGE_STYLE,
    DEFAULT_EDGE_TYPE,
    EDGE_STYLES,
    FONT_FACE,
    NODE_COLORS,
)
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.pyvis_export")

# 类型别名
PathLike = Union[str, Path]


# ══════════════════════════════════════════════════
#  内部工具
# ══════════════════════════════════════════════════
def _normalize_node_states(
    node_states: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """确保 node_states 的 key 全部为 str"""
    if not node_states:
        return {}
    return {str(k): v for k, v in node_states.items() if v is not None}


def _shorten_label(label: str, max_width: int = 60) -> str:
    """
    按 Unicode 显示宽度截断 label。
    ✅ P1-6：累积宽度，O(n) 复杂度。
    """
    if not label:
        return label
    if wcswidth(label) <= max_width:
        return label

    reserve = 3   # "..." 的宽度
    truncated = ""
    current = 0
    for ch in label:
        w = wcswidth(ch)
        if w < 0:
            w = 1
        if current + w > max_width - reserve:
            break
        truncated += ch
        current += w
    return truncated + "..."


def _escape_dot_text(s: str) -> str:
    """转义 DOT 字符串中的特殊字符"""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _get_edge_type(edge: Dict) -> str:
    """
    从 edge 字典提取边类型。
    查找顺序：edge["type"] > edge["edge_type"] > DEFAULT_EDGE_TYPE
    大小写归一，未知类型降级为默认。
    """
    raw = edge.get("type") or edge.get("edge_type") or DEFAULT_EDGE_TYPE
    normalized = str(raw).strip().lower()
    if normalized in EDGE_STYLES:
        return normalized
    logger.debug(
        "[Export] 未知边类型 %r (from=%s, to=%s)，降级为 %s",
        raw, edge.get("from"), edge.get("to"), DEFAULT_EDGE_TYPE,
    )
    return DEFAULT_EDGE_TYPE


def _estimate_canvas_size(
    nodes: List[Dict],
    edges: List[Dict],
    max_width: int = 2400,
    max_height: int = 2000,
    min_width: int = 800,
    min_height: int = 600,
) -> Tuple[int, int]:
    """根据节点/边数动态估算画布尺寸"""
    n = max(len(nodes), 1)
    e = max(len(edges), 1)

    width = min(max_width, max(min_width, n * 180 + 200))
    height = min(max_height, max(min_height, n * 140 + 200))

    if e > n * 1.5:
        stretch = min(1.8, 1.0 + (e - n * 1.5) / (n * 2))
        height = int(height * stretch)

    width = min(width, max_width)
    height = min(height, max_height)

    logger.debug(
        "[Export] 画布尺寸估算: nodes=%d, edges=%d → %dx%d",
        n, e, width, height,
    )
    return width, height


def _build_pydot_graph(
    nodes: List[Dict],
    edges: List[Dict],
    node_states: Dict[str, str],
    title: str,
    dpi: int,
    font_size: int = 14,
) -> Any:
    """
    统一构建 pydot 图对象（供 SVG / PNG / PDF / DOT 共用）。

    Raises:
        ImportError: pydot 或 networkx 未安装
        ValueError:  节点数据无效
    """
    try:
        import networkx as nx
        import pydot  # noqa: F401  （networkx 会用）
    except ImportError as e:
        raise ImportError(
            "导出需要安装 pydot 和 networkx: pip install pydot networkx"
        ) from e

    if not nodes:
        raise ValueError("没有节点数据可导出")

    G = nx.DiGraph()

    # ── 节点 ──
    for node in nodes:
        nid = str(node.get("id") or "").strip()
        if not nid:
            logger.warning("[Export] 跳过无 ID 的节点: %r", node)
            continue

        raw_label = node.get("label") or node.get("name") or nid
        label = _shorten_label(str(raw_label), max_width=60)

        state = node_states.get(nid, "pending")
        if state not in NODE_COLORS:
            state = "pending"

        color_bg, color_border, color_text = NODE_COLORS[state]

        G.add_node(
            nid,
            label=label,
            style="filled,rounded",
            fillcolor=color_bg,
            color=color_border,
            penwidth="2",
            fontcolor=color_text,
            fontname=FONT_FACE,
            fontsize=str(font_size),
            shape="box",
            margin="0.3,0.15",
        )

    # ── 边 ──
    added: set = set()
    for edge in edges:
        src = str(edge.get("from") or edge.get("source") or "").strip()
        tgt = str(edge.get("to") or edge.get("target") or "").strip()
        if not src or not tgt:
            continue
        if (src, tgt) in added:
            continue
        added.add((src, tgt))

        if src not in G.nodes:
            logger.warning("[Export] 边指向不存在的源节点: %s", src)
            continue
        if tgt not in G.nodes:
            logger.warning("[Export] 边指向不存在的目标节点: %s", tgt)
            continue

        edge_type = _get_edge_type(edge)
        style_info = EDGE_STYLES.get(edge_type, DEFAULT_EDGE_STYLE)

        edge_label = edge.get("label", "")
        edge_attrs = {
            "label": f" {_escape_dot_text(edge_label)} " if edge_label else "",
            "color": style_info["color"],
            "penwidth": str(style_info["width"]),
            "fontname": FONT_FACE,
            "fontsize": "11",
            "fontcolor": "#555555",
        }
        if style_info["style"] == "dashed":
            edge_attrs["style"] = "dashed"

        G.add_edge(src, tgt, **edge_attrs)

    # ── 转 pydot ──
    dot_graph = nx.nx_pydot.to_pydot(G)

    dot_graph.obj_dict["attributes"].update({
        "dpi": str(dpi),
        "bgcolor": "white",
        "rankdir": "TB",
        "nodesep": "0.8",
        "ranksep": "1.2",
        "pad": "0.5",
        "label": _escape_dot_text(title),
        "labelloc": "t",
        "fontname": FONT_FACE,
        "fontsize": "18",
        "splines": "true",
        "overlap": "false",
    })

    return dot_graph


def _write_if_requested(data: bytes, output_path: Optional[PathLike]) -> None:
    """可选写文件（自动建目录）"""
    if output_path is None:
        return
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


# ══════════════════════════════════════════════════
#  导出接口
# ══════════════════════════════════════════════════
def export_dag_to_svg(
    nodes: List[Dict],
    edges: List[Dict],
    output_path: Optional[PathLike] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    dpi: int = 96,
    font_size: int = 14,
) -> bytes:
    """
    导出 DAG 为 SVG 字节流，可选写入文件。

    dpi: SVG 物理尺寸计算 DPI（Graphviz 默认 96，越高物理尺寸越小）
    """
    if not nodes:
        raise ValueError("没有节点数据可导出")

    states = _normalize_node_states(node_states)
    dot_graph = _build_pydot_graph(
        nodes, edges, states, title, dpi, font_size
    )
    svg_bytes = dot_graph.create_svg(prog="dot")

    _write_if_requested(svg_bytes, output_path)
    if output_path is not None:
        logger.info(
            "[Export] ✅ SVG 已保存: %s (%.1f KB)",
            output_path, len(svg_bytes) / 1024,
        )
    return svg_bytes


def export_dag_to_png(
    nodes: List[Dict],
    edges: List[Dict],
    output_path: Optional[PathLike] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    width: Optional[int] = None,
    height: Optional[int] = None,
    dpi: int = 300,
    font_size: int = 14,
) -> bytes:
    """
    导出 DAG 为 PNG 字节流，可选写入文件。

    尺寸策略（优先级从高到低）：
      1. width 和 height 都指定 → 用指定值，dpi 忽略
      2. 只指定一个 → 按估算比例算另一个，dpi 忽略
      3. 都不指定 → 估算尺寸 × dpi/96 作为输出像素尺寸
    """
    if not nodes:
        raise ValueError("没有节点数据可导出")

    try:
        import cairosvg
    except ImportError as e:
        raise ImportError(
            "导出 PNG 需要安装 cairosvg: pip install cairosvg\n"
            "系统依赖: apt install libcairo2-dev pkg-config python3-dev"
        ) from e

    states = _normalize_node_states(node_states)
    dot_graph = _build_pydot_graph(
        nodes, edges, states, title, dpi, font_size
    )
    svg_bytes = dot_graph.create_svg(prog="dot")

    est_w, est_h = _estimate_canvas_size(nodes, edges)
    ratio = est_w / est_h if est_h else 1.0

    if width is not None and height is not None:
        final_w, final_h = width, height
    elif width is not None:
        final_w = width
        final_h = int(width / ratio) if ratio > 0 else est_h
    elif height is not None:
        final_h = height
        final_w = int(height * ratio)
    else:
        scale = dpi / 96.0
        final_w = int(est_w * scale)
        final_h = int(est_h * scale)

    logger.debug(
        "[Export] PNG 渲染尺寸: %dx%d (dpi=%d, est=%dx%d)",
        final_w, final_h, dpi, est_w, est_h,
    )

    buf = io.BytesIO()
    cairosvg.svg2png(
        bytestring=svg_bytes,
        write_to=buf,
        output_width=final_w,
        output_height=final_h,
    )
    png_bytes = buf.getvalue()

    _write_if_requested(png_bytes, output_path)
    if output_path is not None:
        logger.info(
            "[Export] ✅ PNG 已保存: %s (%.1f KB)",
            output_path, len(png_bytes) / 1024,
        )
    return png_bytes


def export_dag_to_pdf(
    nodes: List[Dict],
    edges: List[Dict],
    output_path: Optional[PathLike] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    dpi: int = 300,
    font_size: int = 14,
) -> bytes:
    """导出 DAG 为 PDF 字节流，可选写入文件。"""
    if not nodes:
        raise ValueError("没有节点数据可导出")

    try:
        import cairosvg
    except ImportError as e:
        raise ImportError(
            "导出 PDF 需要安装 cairosvg: pip install cairosvg"
        ) from e

    states = _normalize_node_states(node_states)
    dot_graph = _build_pydot_graph(
        nodes, edges, states, title, dpi, font_size
    )
    svg_bytes = dot_graph.create_svg(prog="dot")

    buf = io.BytesIO()
    cairosvg.svg2pdf(
        bytestring=svg_bytes,
        write_to=buf,
        dpi=dpi,
    )
    pdf_bytes = buf.getvalue()

    _write_if_requested(pdf_bytes, output_path)
    if output_path is not None:
        logger.info(
            "[Export] ✅ PDF 已保存: %s (%.1f KB)",
            output_path, len(pdf_bytes) / 1024,
        )
    return pdf_bytes


def export_dag_to_dot(
    nodes: List[Dict],
    edges: List[Dict],
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    dpi: int = 300,
    font_size: int = 14,
) -> str:
    """
    导出 DAG 为 DOT 源码字符串。

    ⚠️ 本函数**不接受 output_path**——DOT 是文本格式，
       由调用方决定如何写入/传输。
    """
    if not nodes:
        raise ValueError("没有节点数据可导出")

    states = _normalize_node_states(node_states)
    dot_graph = _build_pydot_graph(
        nodes, edges, states, title, dpi, font_size
    )
    return dot_graph.to_string()


def export_from_dag_store(
    dag_store: Dict,
    output_path: Optional[PathLike] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    fmt: str = "png",
    **kwargs,
) -> bytes:
    """
    便捷入口：从 dag_store 字典直接导出。

    Args:
        dag_store:    {"nodes": [...], "edges": [...]}
        output_path:  输出文件路径（dot 格式也支持）
        node_states:  节点状态覆盖
        title:        图表标题
        fmt:          "svg" | "png" | "pdf" | "dot"
        **kwargs:     传给具体导出函数的额外参数（如 width/height/dpi）

    Returns:
        导出的字节流（dot 也返回 UTF-8 编码字节，保持接口一致）
    """
    nodes = dag_store.get("nodes", [])
    edges = dag_store.get("edges", [])

    if not nodes:
        raise ValueError("dag_store 中没有节点数据")

    if fmt == "dot":
        dot_str = export_dag_to_dot(
            nodes=nodes,
            edges=edges,
            node_states=node_states,
            title=title,
            **kwargs,
        )
        if output_path is not None:
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(dot_str, encoding="utf-8")
            logger.info("[Export] ✅ DOT 已保存: %s", output_path)
        return dot_str.encode("utf-8")

    fmt_map = {
        "svg": export_dag_to_svg,
        "png": export_dag_to_png,
        "pdf": export_dag_to_pdf,
    }

    exporter = fmt_map.get(fmt)
    if exporter is None:
        raise ValueError(
            f"不支持的格式: {fmt}，支持: {list(fmt_map.keys()) + ['dot']}"
        )

    return exporter(
        nodes=nodes,
        edges=edges,
        output_path=output_path,
        node_states=node_states,
        title=title,
        **kwargs,
    )


__all__ = [
    "export_dag_to_svg",
    "export_dag_to_png",
    "export_dag_to_pdf",
    "export_dag_to_dot",
    "export_from_dag_store",
]
