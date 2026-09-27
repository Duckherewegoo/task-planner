"""
pyvis_export.py — DAG 静态可视化导出引擎 (Graphviz Backend) v2.0
===============================================================
纯后端静态渲染，基于 Graphviz/pydot + cairosvg。
输出 SVG/PNG/PDF/DOT 字节流或文件，适用于报告、邮件、PPT、CI。

与 flowchart_pro.py 的关系：
  - flowchart_pro: 交互式 HTML (PyVis + vis.js)
  - pyvis_export:  静态导出 (Graphviz + cairosvg)

调用方式：所有导出函数均为同步，请用 asyncio.to_thread 隔离阻塞 I/O。
"""
import io
import os
import logging
from typing import Dict, List, Optional, Tuple, Union
from pathlib import Path

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
    STATUS_COLOR,
    STATUS_BORDER,
    STATUS_TEXT,
    NODE_TEXT_COLORS,
    NODE_COLORS,
    EDGE_STYLES,
    DEFAULT_EDGE_STYLE,
    FONT_FACE,
    RENDER_EDGE_WIDTH,
    RENDER_DPI,
    RENDER_WIDTH,
    RENDER_HEIGHT,
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


def _estimate_canvas_size(
    nodes: List[Dict],
    edges: List[Dict],
    max_width: int = 2400,
    max_height: int = 2000,
    min_width: int = 800,
    min_height: int = 600,
) -> Tuple[int, int]:
    """
    根据节点数和边数动态估算画布尺寸（优化版）。

    算法：
      - 基础尺寸由节点数决定 (n * 180, n * 140)
      - 边数较多时按比例拉伸高度 (e > n * 1.5 时)
      - 限制在 [min, max] 范围内
    """
    n = max(len(nodes), 1)
    e = max(len(edges), 1)

    # 基础尺寸
    width = min(max_width, max(min_width, n * 180 + 200))
    height = min(max_height, max(min_height, n * 140 + 200))

    # 边数较多时拉伸高度
    if e > n * 1.5:
        stretch_factor = min(1.8, 1.0 + (e - n * 1.5) / (n * 2))
        height = int(height * stretch_factor)

    # 确保不超过最大值
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
    统一构建 pydot 图对象（供 SVG / PNG / PDF 共用）。
    返回 pydot.Dot 实例。

    Raises:
        ImportError: pydot 未安装
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
        nid = str(node.get("id"))
        if not nid:
            logger.warning("[Export] 跳过无 ID 的节点: %s", node)
            continue

        label = node.get("label") or node.get("name") or nid
        # 标签过长时截断（避免渲染溢出）
        if len(label) > 60:
            label = label[:57] + "..."

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
        src = str(edge.get("from") or edge.get("source"))
        tgt = str(edge.get("to") or edge.get("target"))
        if not src or not tgt:
            continue
        if (src, tgt) in added:
            continue
        added.add((src, tgt))

        # 检查节点是否存在
        if src not in G.nodes:
            logger.warning("[Export] 边指向不存在的源节点: %s", src)
            continue
        if tgt not in G.nodes:
            logger.warning("[Export] 边指向不存在的目标节点: %s", tgt)
            continue

        edge_label = edge.get("label", "")
        state = edge.get("state", node_states.get(src, "pending"))
        style_info = EDGE_STYLES.get(state, DEFAULT_EDGE_STYLE)

        edge_attrs = {
            "label": f" {edge_label} " if edge_label else "",
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
    dot_graph.obj_dict["attributes"].update({
        "dpi": str(dpi),
        "bgcolor": "white",
        "rankdir": "TB",
        "nodesep": "0.8",
        "ranksep": "1.2",
        "pad": "0.5",
        "label": title,
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
    dpi: int = 800,
    font_size: int = 14,
) -> bytes:
    """
    导出 DAG 为 SVG 字节流，可选写入文件。

    Args:
        nodes: 节点列表 [{"id": 1, "name": "步骤1", "status": 0}, ...]
        edges: 边列表 [{"from": 1, "to": 2, "label": "依赖"}]
        output_path: 可选输出文件路径
        node_states: 节点状态覆盖 {"1": "done", "2": "failed"}
        title: 图表标题
        dpi: 渲染 DPI (越高越清晰，默认 800)
        font_size: 节点字体大小 (默认 14)

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

    尺寸策略 (优先级从高到低)：
      1. 如果 width 和 height 都指定 → 使用指定尺寸
      2. 如果只指定其中一个 → 按原图比例计算另一个
      3. 都未指定 → 使用估算尺寸 (基于节点数自适应)

    Args:
        nodes: 节点列表
        edges: 边列表
        output_path: 可选输出文件路径
        node_states: 节点状态覆盖
        title: 图表标题
        width: 输出宽度 (像素)，不指定则自动估算
        height: 输出高度 (像素)，不指定则自动估算
        dpi: 渲染 DPI (仅影响字体大小，实际尺寸由 width/height 控制)
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
        if width is None and height is None:
            # 自动估算
            est_w, est_h = _estimate_canvas_size(nodes, edges)
            width, height = est_w, est_h
        elif width is None:
            # 只有 height，按比例计算 width（从 SVG 中提取比例）
            # 简化：使用估算比例
            est_w, est_h = _estimate_canvas_size(nodes, edges)
            ratio = est_w / est_h
            width = int(height * ratio)
        elif height is None:
            est_w, est_h = _estimate_canvas_size(nodes, edges)
            ratio = est_w / est_h
            height = int(width / ratio)

        logger.debug(
            "[Export] PNG 渲染尺寸: %dx%d @ %d DPI",
            width, height, dpi,
        )

        # ── 3. 渲染 PNG ──
        buf = io.BytesIO()
        cairosvg.svg2png(
            bytestring=svg_bytes,
            write_to=buf,
            output_width=width,
            output_height=height,
            dpi=dpi,
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

    Args:
        nodes: 节点列表
        edges: 边列表
        node_states: 节点状态覆盖
        title: 图表标题
        dpi: 渲染 DPI
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
    output_path: Union[str, Path],
    node_states: Optional[Dict[str, str]] = None,
    title: str = "流程图",
    fmt: str = "png",
    **kwargs,
) -> bytes:
    """
    便捷入口：从 dag_store 字典直接导出。

    Args:
        dag_store: {"nodes": [...], "edges": [...]}
        output_path: 输出文件路径
        node_states: 节点状态覆盖
        title: 图表标题
        fmt: 导出格式 ("svg" | "png" | "pdf" | "dot")
        **kwargs: 传递给具体导出函数的额外参数

    Returns:
        导出的字节流 (对于 dot 格式返回 UTF-8 编码的字符串)
    """
    nodes = dag_store.get("nodes", [])
    edges = dag_store.get("edges", [])

    if not nodes:
        raise ValueError("dag_store 中没有节点数据")

    fmt_map = {
        "svg": export_dag_to_svg,
        "png": export_dag_to_png,
        "pdf": export_dag_to_pdf,
        "dot": export_dag_to_dot,
    }

    if fmt not in fmt_map:
        raise ValueError(
            f"不支持的格式: {fmt}，支持: {list(fmt_map.keys())}"
        )

    exporter = fmt_map[fmt]
    result = exporter(
        nodes=nodes,
        edges=edges,
        output_path=output_path,
        node_states=node_states,
        title=title,
        **kwargs,
    )

    # DOT 格式返回字符串，需要编码为字节
    if fmt == "dot" and isinstance(result, str):
        return result.encode("utf-8")

    return result


# ══════════════════════════════════════════════════
#  已废弃：_render_pyvis_html
# ══════════════════════════════════════════════════
# 该函数已迁移至 flowchart_pro.py，保留此注释以免历史引用断裂。
# 如有调用，请改用 flowchart_pro.render_interactive()。
# def _render_pyvis_html(...):
#     # 已废弃
#     pass
