"""
pyvis_export.py — DAG 静态可视化导出引擎 (Graphviz Backend) v3.0
===============================================================
纯后端静态渲染，基于 Graphviz/pydot + cairosvg。
输出 SVG/PNG/PDF/DOT 字节流或文件，适用于报告、邮件、PPT、CI。

与 flowchart_pro.py 的关系：
  - flowchart_pro: 交互式 HTML (PyVis + vis.js)
  - pyvis_export:  静态导出 (Graphviz + cairosvg)

调用方式：所有导出函数均为同步，请用 asyncio.to_thread 隔离阻塞 I/O。

Changelog:
  ✅ P0-1：export_from_dag_store 的 dot 分支不再向 export_dag_to_dot
           传递 output_path（该函数不接受此参数）。
  ✅ P0-2：边样式按 edge["type"]（hard/soft/conditional/retry）区分，
           不再错误地使用 node_state（pending/done/failed）当 key。
  ✅ P1-1：export_dag_to_png 的 dpi 参数现在真正生效——
           未指定 width/height 时按 dpi/96 换算像素尺寸；
           指定宽高时以宽高为准（dpi 忽略，符合 cairosvg 语义）。
  ✅ P1-2：export_dag_to_svg 默认 dpi 从 800 改为 96，
           避免 Graphviz 输出物理尺寸过小（1 像素=1/800 英寸）。
  ✅ P1-3：节点 label 截断改用 wcswidth 显示宽度，
           中文不再按字符数截断导致溢出。
  ✅ P1-4：title 注入 DOT 前用 _escape_dot_text 转义引号/反斜杠/换行。
  ✅ P2-1：删除未使用的 config 导入
           (STATUS_COLOR / STATUS_BORDER / STATUS_TEXT / NODE_TEXT_COLORS /
            RENDER_EDGE_WIDTH / RENDER_DPI / RENDER_WIDTH / RENDER_HEIGHT)。
  ✅ P2-3：跳过无 ID 节点时打印节点内容，便于排查。
"""
import io
import os
import logging
from typing import Dict, List, Optional, Tuple, Union
from pathlib import Path

from wcwidth import wcswidth

try:
    from task_planner.infrastructure.logger_setup import get_logger
    logger = get_logger()
except ImportError:
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════════
#  从 config 统一导入（与 flowchart_pro 共享配置）
# ══════════════════════════════════════════════════
from task_planner.infrastructure.config import (
    NODE_COLORS,
    EDGE_STYLES,
    DEFAULT_EDGE_STYLE,
    DEFAULT_EDGE_TYPE,
    FONT_FACE,
)


# ══════════════════════════════════════════════════
#  内部工具函数
# ══════════════════════════════════════════════════

def _normalize_node_states(
    node_states: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """确保 node_states 的 key 全部为 str，避免类型不匹配"""
    if not node_states:
        return {}
    return {str(k): v for k, v in node_states.items() if v is not None}


# ✅ P1-3 修复：按显示宽度截断
def _shorten_label(label: str, max_width: int = 60) -> str:
    """
    按 Unicode 显示宽度截断 label（与 flowchart_pro.py 一致）。

    为什么不用 len()：
      "中文" 的 len 是 2，但 wcswidth 是 4。
      用 len 截断会导致中文标签超宽溢出。
    """
    if not label:
        return label
    if wcswidth(label) <= max_width:
        return label

    truncated = ""
    reserve = 3   # "..." 的宽度
    for ch in label:
        w = wcswidth(ch)
        if w < 0:
            w = 1
        if wcswidth(truncated + ch) > max_width - reserve:
            break
        truncated += ch
    return truncated + "..."


# ✅ P1-4 修复：DOT 文本转义
def _escape_dot_text(s: str) -> str:
    """
    转义 DOT 字符串中的特殊字符。

    pydot 会做部分转义，但对嵌套引号和换行处理不完整。
    主动转义以防 title / label 含特殊字符时破坏 DOT 语法。
    """
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )


# ✅ P0-2 修复：按边类型提取样式（而非节点状态）
def _get_edge_type(edge: Dict) -> str:
    """
    从 edge 字典提取边类型。

    查找顺序：
      1. edge["type"]
      2. edge["edge_type"]
      3. 默认 DEFAULT_EDGE_TYPE（通常是 "hard"）

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
    """
    根据节点数和边数动态估算画布尺寸。

    算法：
      - 基础尺寸由节点数决定 (n * 180, n * 140)
      - 边数较多时按比例拉伸高度 (e > n * 1.5 时)
      - 限制在 [min, max] 范围内
    """
    n = max(len(nodes), 1)
    e = max(len(edges), 1)

    width = min(max_width, max(min_width, n * 180 + 200))
    height = min(max_height, max(min_height, n * 140 + 200))

    if e > n * 1.5:
        stretch_factor = min(1.8, 1.0 + (e - n * 1.5) / (n * 2))
        height = int(height * stretch_factor)

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
) -> "pydot.Dot":
    """
    统一构建 pydot 图对象（供 SVG / PNG / PDF / DOT 共用）。

    Raises:
        ImportError: pydot 或 networkx 未安装
        ValueError: 节点数据无效
    """
    try:
        import pydot
        import networkx as nx
    except ImportError as e:
        raise ImportError(
            "导出需要安装 pydot 和 networkx: pip install pydot networkx"
        ) from e

    if not nodes:
        raise ValueError("没有节点数据可导出")

    G = nx.DiGraph()

    # ── 添加节点 ──
    for node in nodes:
        nid = str(node.get("id") or "").strip()
        if not nid:
            # ✅ P2-3 修复：打印节点内容便于排查
            logger.warning("[Export] 跳过无 ID 的节点: %r", node)
            continue

        raw_label = node.get("label") or node.get("name") or nid
        # ✅ P1-3 修复：按显示宽度截断
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

    # ── 添加边 ──
    added = set()
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

        # ✅ P0-2 修复：按 edge 的类型（hard/soft/conditional/retry）取样式，
        #    不再错误地用 node_state（pending/done/failed）当 key。
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

    # ── 转换为 pydot ──
    dot_graph = nx.nx_pydot.to_pydot(G)

    # ── 设置全局属性 ──
    # ✅ P1-4 修复：title 转义
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


# ══════════════════════════════════════════════════
#  对外导出接口（全部同步，调用方用 asyncio.to_thread）
# ══════════════════════════════════════════════════

def export_dag_to_svg(
    nodes: List[Dict],
    edges: List[Dict],
    output_path: Optional[Union[str, Path]] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    dpi: int = 96,
    font_size: int = 14,
) -> bytes:
    """
    导出 DAG 为 SVG 字节流，可选写入文件。

    Args:
        nodes: 节点列表 [{"id": 1, "name": "步骤1", "status": 0}, ...]
        edges: 边列表 [{"from": 1, "to": 2, "label": "依赖", "type": "hard"}]
        output_path: 可选输出文件路径
        node_states: 节点状态覆盖 {"1": "done", "2": "failed"}
        title: 图表标题
        dpi: SVG 物理尺寸计算 DPI（Graphviz 默认 96，越高物理尺寸越小）
        font_size: 节点字体大小

    Returns:
        SVG 字节流

    Raises:
        ImportError: pydot 或 networkx 未安装
        ValueError: 节点数据无效
    """
    if not nodes:
        raise ValueError("没有节点数据可导出")

    try:
        states = _normalize_node_states(node_states)
        dot_graph = _build_pydot_graph(
            nodes, edges, states, title, dpi, font_size
        )
        svg_bytes = dot_graph.create_svg(prog="dot")

        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(svg_bytes)
            logger.info(
                "[Export] ✅ SVG 已保存: %s (%.1f KB)",
                output_path, len(svg_bytes) / 1024,
            )

        return svg_bytes

    except Exception as e:
        logger.error("[Export] SVG 导出失败: %s", e, exc_info=True)
        raise


def export_dag_to_png(
    nodes: List[Dict],
    edges: List[Dict],
    output_path: Optional[Union[str, Path]] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    width: Optional[int] = None,
    height: Optional[int] = None,
    dpi: int = 300,
    font_size: int = 14,
) -> bytes:
    """
    导出 DAG 为 PNG 字节流，可选写入文件。

    ✅ P1-1 修复：dpi 现在真正生效。
      尺寸策略（优先级从高到低）：
        1. width 和 height 都指定 → 用指定值，dpi 忽略
        2. 只指定一个 → 按估算比例算另一个，dpi 忽略
        3. 都不指定 → 用估算尺寸 × dpi/96 作为输出像素尺寸
          （96 是 SVG 的标准 DPI，dpi=192 相当于 2 倍缩放）

    Args:
        nodes: 节点列表
        edges: 边列表
        output_path: 可选输出文件路径
        node_states: 节点状态覆盖
        title: 图表标题
        width: 输出宽度（像素），不指定则按 dpi 换算
        height: 输出高度（像素），不指定则按 dpi 换算
        dpi: 渲染 DPI（仅在未指定宽高时生效）
        font_size: 节点字体大小

    Returns:
        PNG 字节流
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

    try:
        # ── 1. 生成 SVG ──
        states = _normalize_node_states(node_states)
        dot_graph = _build_pydot_graph(
            nodes, edges, states, title, dpi, font_size
        )
        svg_bytes = dot_graph.create_svg(prog="dot")

        # ── 2. 确定输出尺寸 ──
        est_w, est_h = _estimate_canvas_size(nodes, edges)
        ratio = est_w / est_h if est_h else 1.0

        if width is not None and height is not None:
            # 双指定：用指定值
            final_w, final_h = width, height
        elif width is not None:
            # 只指定宽：按比例算高
            final_w = width
            final_h = int(width / ratio) if ratio > 0 else est_h
        elif height is not None:
            # 只指定高：按比例算宽
            final_h = height
            final_w = int(height * ratio)
        else:
            # 都没指定：按 dpi 换算（96 是 SVG 标准 DPI）
            scale = dpi / 96.0
            final_w = int(est_w * scale)
            final_h = int(est_h * scale)

        logger.debug(
            "[Export] PNG 渲染尺寸: %dx%d (dpi=%d, est=%dx%d)",
            final_w, final_h, dpi, est_w, est_h,
        )

        # ── 3. 渲染 PNG ──
        buf = io.BytesIO()
        cairosvg.svg2png(
            bytestring=svg_bytes,
            write_to=buf,
            output_width=final_w,
            output_height=final_h,
            # 不传 dpi：cairosvg 在指定宽高时忽略 dpi
        )
        png_bytes = buf.getvalue()

        # ── 4. 可选写入文件 ──
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(png_bytes)
            logger.info(
                "[Export] ✅ PNG 已保存: %s (%.1f KB)",
                output_path, len(png_bytes) / 1024,
            )

        return png_bytes

    except Exception as e:
        logger.error("[Export] PNG 导出失败: %s", e, exc_info=True)
        raise


def export_dag_to_pdf(
    nodes: List[Dict],
    edges: List[Dict],
    output_path: Optional[Union[str, Path]] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    dpi: int = 300,
    font_size: int = 14,
) -> bytes:
    """
    导出 DAG 为 PDF 字节流，可选写入文件。

    Args:
        nodes: 节点列表
        edges: 边列表
        output_path: 可选输出文件路径
        node_states: 节点状态覆盖
        title: 图表标题
        dpi: 渲染 DPI
        font_size: 节点字体大小

    Returns:
        PDF 字节流
    """
    if not nodes:
        raise ValueError("没有节点数据可导出")

    try:
        import cairosvg
    except ImportError as e:
        raise ImportError(
            "导出 PDF 需要安装 cairosvg: pip install cairosvg"
        ) from e

    try:
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

        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(pdf_bytes)
            logger.info(
                "[Export] ✅ PDF 已保存: %s (%.1f KB)",
                output_path, len(pdf_bytes) / 1024,
            )

        return pdf_bytes

    except Exception as e:
        logger.error("[Export] PDF 导出失败: %s", e, exc_info=True)
        raise


def export_dag_to_dot(
    nodes: List[Dict],
    edges: List[Dict],
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    dpi: int = 300,
    font_size: int = 14,
) -> str:
    """
    导出 DAG 为 DOT 源码字符串（用于调试或二次处理）。

    ⚠️ 注意：本函数**不接受 output_path**——DOT 是文本格式，
       由调用方决定如何写入/传输。这与 export_dag_to_svg/png/pdf 不同，
       它们的 output_path 是可选的副作用，而 DOT 的主产物就是字符串。

    Args:
        nodes: 节点列表
        edges: 边列表
        node_states: 节点状态覆盖
        title: 图表标题
        dpi: 渲染 DPI（写进 DOT 属性）
        font_size: 节点字体大小

    Returns:
        DOT 源码字符串
    """
    if not nodes:
        raise ValueError("没有节点数据可导出")

    try:
        states = _normalize_node_states(node_states)
        dot_graph = _build_pydot_graph(
            nodes, edges, states, title, dpi, font_size
        )
        return dot_graph.to_string()

    except Exception as e:
        logger.error("[Export] DOT 导出失败: %s", e, exc_info=True)
        raise


def export_from_dag_store(
    dag_store: Dict,
    output_path: Optional[Union[str, Path]] = None,
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    fmt: str = "png",
    **kwargs,
) -> bytes:
    """
    便捷入口：从 dag_store 字典直接导出。

    ✅ P0-1 修复：dot 格式不再向 export_dag_to_dot 传递 output_path
      （该函数不接受此参数）。

    Args:
        dag_store: {"nodes": [...], "edges": [...]}
        output_path: 输出文件路径（dot 格式忽略；其他格式可选）
        node_states: 节点状态覆盖
        title: 图表标题
        fmt: 导出格式 ("svg" | "png" | "pdf" | "dot")
        **kwargs: 传递给具体导出函数的额外参数（如 width/height/dpi）

    Returns:
        导出的字节流（dot 也返回 UTF-8 编码字节，保持接口一致）
    """
    nodes = dag_store.get("nodes", [])
    edges = dag_store.get("edges", [])

    if not nodes:
        raise ValueError("dag_store 中没有节点数据")

    # ✅ P0-1 修复：dot 单独处理，因为它不接受 output_path
    if fmt == "dot":
        dot_str = export_dag_to_dot(
            nodes=nodes,
            edges=edges,
            node_states=node_states,
            title=title,
            **kwargs,
        )
        # 可选写入文件
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(dot_str, encoding="utf-8")
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


# ══════════════════════════════════════════════════
#  已废弃：_render_pyvis_html
# ══════════════════════════════════════════════════
# 该函数已迁移至 flowchart_pro.py，保留此注释以免历史引用断裂。
# 如有调用，请改用 flowchart_pro.render_interactive()。
# def _render_pyvis_html(...):
#     # 已废弃
#     pass
