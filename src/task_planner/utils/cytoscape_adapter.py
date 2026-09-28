"""
cytoscape_adapter.py — DAG → Cytoscape.js 数据适配器
支持循环边、动态边距、Markdown 友好数据、边类型(hard/soft/conditional/retry)

Changelog:
  ✅ P0-1：新增 _char_width 函数，修复 _short_label 里的 NameError
  ✅ P0-2：_detect_cycles_cached 调用前先转 tuple，修复 lru_cache 的 unhashable 错误
  ✅ P0-3：补上 edge_type 变量赋值（data.edge_type）
  ✅ P1-1：_detect_cycles 返回类型注解修正为 Set[Tuple[str, str]]
  ✅ P1-2：删除 _node_state_class 未使用的 in_degree 参数
  ✅ P1-3：删除 _edge_class 未使用的 node_states 参数
  ✅ P1-4：_is_loop_edge 脏数据 warning 用 hasattr 去重（每类只 warning 一次）
  ✅ P1-5：build_history_detail_markdown 的 icon_map 改用 config.STATUS_ICONS
  ✅ P1-6：统一用 _safe_int 处理 status
  ✅ P1-7：_status_icon 用 NODE_STYLES.get(0, {}) 兜底
"""
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple

from wcwidth import wcswidth

from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.config import (
    EDGE_TYPE_HARD, EDGE_TYPE_SOFT,
    EDGE_TYPE_CONDITIONAL, EDGE_TYPE_RETRY,
    DEFAULT_EDGE_TYPE, EDGE_TYPE_CSS, VALID_EDGE_TYPES, NODE_STYLES,
    STATUS_ICONS,
)

logger = get_logger("task_planner.cytoscape_adapter")

_VALID_EDGE_TYPES = VALID_EDGE_TYPES


# ══════════════════════════════════════════════════
#  ✅ P0-1 修复：新增 _char_width（_short_label 依赖它）
# ══════════════════════════════════════════════════
def _char_width(ch: str) -> int:
    """
    单字符显示宽度。wcswidth 对不可打印字符返回 -1，统一按 1 处理。
    """
    w = wcswidth(ch)
    return w if w >= 0 else 1


def _label_length(label: str) -> int:
    """
    基于 Unicode East Asian Width 属性的精确显示宽度计算。
    正确处理 CJK、Emoji、组合字符、零宽字符。
    """
    if not label:
        return 0
    w = wcswidth(label)
    # wcswidth 对包含不可打印字符的字符串返回 -1
    return max(w, len(label))


# ══════════════════════════════════════════════════
#  循环检测（带缓存）
# ══════════════════════════════════════════════════
@lru_cache(maxsize=32)
def _detect_cycles_cached(
    edge_tuple: Tuple[Tuple[str, str], ...],
) -> frozenset:
    """
    ✅ P0-2 修复：调用方必须传 tuple（lru_cache 要求可哈希）。
    返回 frozenset[str]，元素形如 "from->to"。
    """
    edges = [{"from": f, "to": t} for f, t in edge_tuple]
    result = _detect_cycles(edges)
    return frozenset(f"{f}->{t}" for f, t in result)


def _detect_cycles(edges: List[Dict]) -> Set[Tuple[str, str]]:
    """
    ✅ P1-1 修复：返回类型注解修正为 Set[Tuple[str, str]]。
    检测哪些边属于循环/回边（形成环的边）。
    使用迭代版三色 DFS 避免 RecursionError，支持多重边。
    """
    adj: Dict[str, List[Tuple[str, int]]] = {}
    nodes_order_dict: Dict[str, None] = {}

    for idx, e in enumerate(edges):
        f = str(e.get("from", ""))
        t = str(e.get("to", ""))
        if not f or not t:
            continue
        if f not in adj:
            adj[f] = []
        adj[f].append((t, idx))
        nodes_order_dict[f] = None
        nodes_order_dict[t] = None

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes_order_dict}
    cycle_edge_indices: set[int] = set()

    for start_node in nodes_order_dict:
        if color[start_node] != WHITE:
            continue

        stack = [(start_node, iter(adj.get(start_node, [])))]
        color[start_node] = GRAY

        while stack:
            node, neighbors_iter = stack[-1]
            try:
                nxt, edge_idx = next(neighbors_iter)
                if color.get(nxt) == GRAY:
                    cycle_edge_indices.add(edge_idx)
                elif color.get(nxt) == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, iter(adj.get(nxt, []))))
            except StopIteration:
                stack.pop()
                color[node] = BLACK

    cycle_edges: Set[Tuple[str, str]] = set()
    for idx in cycle_edge_indices:
        e = edges[idx]
        cycle_edges.add((str(e.get("from", "")), str(e.get("to", ""))))
    return cycle_edges


# ══════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════

_STOP_WORDS_PATTERN = re.compile(r"(?:后才能|准备好|已完成|完成|完毕|就绪)")


def _safe_int(val: Any, default: int = 0) -> int:
    """✅ P1-6 修复：容忍 None / 字符串 / 非法值"""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _short_label(
    text: Optional[str],
    max_width: int = 24,
    ellipsis: str = "…",
) -> str:
    if not text or not str(text).strip():
        return ""

    cleaned = _STOP_WORDS_PATTERN.sub("", str(text)).strip()
    if not cleaned:
        return ""

    if _label_length(cleaned) <= max_width:
        return cleaned

    truncated = ""
    current_width = 0
    ellipsis_width = _label_length(ellipsis)
    available = max_width - ellipsis_width

    for ch in cleaned:
        ch_width = _char_width(ch)
        if current_width + ch_width > available:
            break
        truncated += ch
        current_width += ch_width

    return truncated + ellipsis


# ✅ P1-4 修复：脏数据 warning 只打一次（按 (src, tgt) 去重）
_warned_empty_endpoints: set[tuple] = set()


def _is_loop_edge(edge: Dict) -> bool:
    """判断是否为自环边。"""
    src = edge.get("from")
    tgt = edge.get("to")

    if src is None or tgt is None:
        return False

    src_clean = str(src).strip()
    tgt_clean = str(tgt).strip()

    if not src_clean or not tgt_clean:
        key = (src, tgt)
        if key not in _warned_empty_endpoints:
            _warned_empty_endpoints.add(key)
            logger.warning(
                "Edge with empty endpoint detected: from=%r, to=%r",
                src, tgt,
            )
        return False

    return src_clean == tgt_clean


def _get_edge_type(edge: dict) -> str:
    """提取并验证边的类型（大小写不敏感，未知降级）。"""
    raw = edge.get("type")
    if raw is None:
        return DEFAULT_EDGE_TYPE

    normalized = str(raw).strip().lower()
    if normalized in _VALID_EDGE_TYPES:
        return normalized

    if normalized:
        logger.warning(
            "Unknown edge type %r (from=%s, to=%s), falling back to %s",
            raw, edge.get("from"), edge.get("to"), DEFAULT_EDGE_TYPE,
        )
    return DEFAULT_EDGE_TYPE


def _status_icon(status_code: int) -> str:
    """
    ✅ P1-7 修复：用 NODE_STYLES.get(0, {}) 兜底，避免 KeyError。
    """
    normalized_code = _safe_int(status_code, default=0)
    default_style = NODE_STYLES.get(0, {})
    style = NODE_STYLES.get(normalized_code, default_style)
    return style.get("icon", "❓")


def _node_state_class(
    nid: str,
    status: int,
    node_states: Dict[str, str],
    done_nodes: Set[str],
    edges: List[Dict],
) -> str:
    """
    ✅ P1-2 修复：删除未使用的 in_degree 参数。
    节点状态 → CSS class。
    """
    user_state = node_states.get(nid)
    if user_state == "done":
        return "state-done"
    elif user_state == "running":
        return "state-running"
    elif user_state == "failed":
        return "state-failed"
    elif user_state == "skipped":
        return "state-skipped"
    elif user_state == "timeout":
        return "state-timeout"

    if status == 2:
        return "state-done"
    elif status == 1:
        return "state-running"
    elif status == 3:
        return "state-failed"
    elif status == 4:
        return "state-timeout"

    my_in_edges = [e for e in edges if str(e.get("to")) == nid]
    hard_in_edges = [
        e for e in my_in_edges if _get_edge_type(e) == EDGE_TYPE_HARD
    ]

    if not hard_in_edges:
        return "state-ready"

    all_hard_done = all(
        str(e.get("from")) in done_nodes for e in hard_in_edges
    )
    return "state-ready" if all_hard_done else "state-blocked"


def _edge_class(edge: dict, is_cycle: bool) -> str:
    """
    ✅ P1-3 修复：删除未使用的 node_states 参数。
    """
    t = _get_edge_type(edge)
    base = EDGE_TYPE_CSS.get(t, EDGE_TYPE_CSS[DEFAULT_EDGE_TYPE])
    classes = [base]

    if is_cycle:
        classes.append("edge-cycle")

    return " ".join(classes)


# ══════════════════════════════════════════════════
#  主转换函数
# ══════════════════════════════════════════════════

def dag_to_cytoscape(
    nodes: List[Dict],
    edges: List[Dict],
    node_states: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    """
    把 DAG 节点/边转成 Cytoscape elements 数组。
    """
    if node_states is None:
        node_states = {}

    elements: List[Dict] = []

    # 已完成节点集合
    done_nodes: Set[str] = {
        nid for nid, st in node_states.items() if st == "done"
    }
    for n in nodes:
        if _safe_int(n.get("status"), default=0) == 2:
            done_nodes.add(str(n["id"]))

    # ✅ P0-2 修复：先转 tuple 再调缓存函数
    edge_tuple = tuple(
        (str(e.get("from", "")), str(e.get("to", ""))) for e in edges
    )
    cycle_edge_ids = _detect_cycles_cached(edge_tuple)

    # ── 节点 ──────────────────────────────
    for node in nodes:
        nid = str(node["id"])
        status = _safe_int(node.get("status"), default=0)
        name = str(node.get("name", f"步骤{node['id']}"))

        user_state = node_states.get(nid)
        if user_state == "done":
            icon = "✅"
        elif user_state == "running":
            icon = "🔄"
        elif user_state == "failed":
            icon = "❌"
        elif user_state == "skipped":
            icon = "⏭️"
        else:
            icon = _status_icon(status)

        label = f"{icon} {name}"

        meta = node.get("meta", {}) or {}
        pre = meta.get("preconditions", []) or []
        post = meta.get("postconditions", []) or []

        cls = _node_state_class(
            nid, status, node_states, done_nodes, edges,
        )

        label_len = _label_length(label)
        node_width = max(120, label_len * 10 + 40)

        elements.append({
            "data": {
                "id": nid,
                "label": label,
                "node_name": name,
                "state": user_state or "pending",
                "status": status,
                "status_text": icon,
                "details": str(
                    node.get("details")
                    or node.get("detail")
                    or "暂无行动细节"
                ),
                "preconditions": [str(p) for p in pre],
                "preconditions_text": (
                    "；".join(str(p) for p in pre) if pre else "无"
                ),
                "postconditions": [str(p) for p in post],
                "postconditions_text": (
                    "；".join(str(p) for p in post) if post else "无"
                ),
                "retry_count": _safe_int(node.get("retry_count"), default=0),
                "retry_policy": str(
                    meta.get("retry_policy", "失败后重试，最多3次")
                ),
                "width": node_width,
            },
            "classes": cls,
        })

    # ── 边 ──────────────────────────────
    for edge in edges:
        f = str(edge.get("from"))
        t = str(edge.get("to"))
        eid = f"{f}->{t}"
        raw_label = str(edge.get("label", ""))
        short = _short_label(raw_label, max_width=16)

        is_cycle = eid in cycle_edge_ids or _is_loop_edge(edge)
        cls = _edge_class(edge, is_cycle)

        if _is_loop_edge(edge):
            cls += " edge-loop"

        label_display_len = _label_length(raw_label)
        edge_text_margin = max(10, min(40, label_display_len * 2))

        # ✅ P0-3 修复：edge_type 变量赋值
        edge_type = _get_edge_type(edge)

        elements.append({
            "data": {
                "id": eid,
                "source": f,
                "target": t,
                "label": short,
                "full_label": raw_label,
                "label_length": label_display_len,
                "text_margin_y": edge_text_margin,
                "edge_type": edge_type,
            },
            "classes": cls,
        })

    logger.debug(
        "[CytoAdapter] ✅ 转换完成 | 节点=%d 边=%d 循环边=%d 状态数=%d",
        len(nodes), len(edges), len(cycle_edge_ids), len(node_states),
    )
    return elements


def snapshot_to_elements(snapshot: Dict) -> List[Dict]:
    """从 agent snapshot 提取 nodes/edges 并转换"""
    nodes = snapshot.get("nodes", []) or []
    edges = snapshot.get("edges", []) or []
    if not nodes:
        return []

    node_states: Dict[str, str] = {}
    node_results = snapshot.get("node_results", []) or []
    current_idx = snapshot.get("current_node_index", 0)

    for i, n in enumerate(nodes):
        nid = str(n["id"])
        st = _safe_int(n.get("status"), default=0)
        if st == 2:
            node_states[nid] = "done"
        elif st == 1:
            node_states[nid] = "running"
        elif st == 3:
            node_states[nid] = "failed"
        elif i == current_idx:
            node_states[nid] = "running"

    for r in node_results:
        nid = str(r.get("node_id", ""))
        if not nid:
            continue
        if r.get("success") is True:
            node_states[nid] = "done"
        elif r.get("success") is False:
            node_states[nid] = "failed"

    return dag_to_cytoscape(nodes, edges, node_states)


# ══════════════════════════════════════════════════
#  详情面板构造（Markdown 格式）
# ══════════════════════════════════════════════════

def build_detail_markdown(node_data: Optional[Dict]) -> str:
    """生成节点详情的 Markdown 文本"""
    if not node_data:
        return "👆 **悬停或点击节点查看详细信息**"

    status = _safe_int(node_data.get("status"), default=0)
    icon = NODE_STYLES.get(status, {}).get("icon", "❓")
    name = node_data.get("node_name", "")
    st_text = node_data.get("status_text", icon)
    details = node_data.get("details", "暂无")
    retry = node_data.get("retry_count", 0)
    policy = node_data.get("retry_policy", "")

    pre = node_data.get("preconditions", []) or []
    post = node_data.get("postconditions", []) or []

    lines = [
        f"## {icon} {name}",
        f"**状态**: `{st_text}`",
        f"**详情**: {details}",
        f"**重试次数**: {retry}",
    ]

    if pre:
        lines.append("")
        lines.append("### 📌 前置条件")
        for p in pre[:8]:
            lines.append(f"- {p}")

    if post:
        lines.append("")
        lines.append("### 🎯 后置条件")
        for p in post[:8]:
            lines.append(f"- {p}")

    if policy:
        lines.append("")
        lines.append(f"**重试策略**: {policy}")

    return "\n".join(lines)


def build_history_detail_markdown(task_id: str, nodes: List[Dict]) -> str:
    """
    生成历史任务详情的 Markdown 文本。

    ✅ P1-5 修复：icon_map 改用 config.STATUS_ICONS，与 STATUS_TEXT 对齐。
    """
    lines = [
        f"## 📋 任务 `{task_id[-8:]}`",
        f"**总节点数**: {len(nodes)}",
        "",
        "### 节点列表",
        "",
        "| 状态 | ID | 名称 |",
        "|---|---|---|",
    ]

    for n in nodes:
        st = _safe_int(n.get("status"), default=0)
        ic = STATUS_ICONS.get(st, "?")
        lines.append(f"| {ic} | #{n['id']} | {n.get('name', '')} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("点击右侧流程图中的节点可查看各节点详情。")

    return "\n".join(lines)
