"""
cytoscape_adapter.py — DAG → Cytoscape.js 数据适配器
支持循环边、动态边距、Markdown 友好数据、边类型(hard/soft/conditional/retry)
"""
from typing import Any, Dict, List, Optional, Set, Tuple
import re
from wcwidth import wcswidth
from functools import lru_cache
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.config import (
    EDGE_TYPE_HARD, EDGE_TYPE_SOFT,
    EDGE_TYPE_CONDITIONAL, EDGE_TYPE_RETRY,
    DEFAULT_EDGE_TYPE, EDGE_TYPE_CSS, VALID_EDGE_TYPES, NODE_STYLES,
)
logger = get_logger("task_planner.cytoscape_adapter")

# 在文件顶部模块级添加
#_cycle_cache = {"hash": None, "result": None}
_VALID_EDGE_TYPES = VALID_EDGE_TYPES


@lru_cache(maxsize=32)
# 修改 _detect_cycles_cached 的返回格式，以便直接匹配 eid
def _detect_cycles_cached(edge_tuple: Tuple[Tuple[str, str], ...]) -> frozenset:
    edges = [{"from": f, "to": t} for f, t in edge_tuple]
    result = _detect_cycles(edges)
    # 转换为 set of "from->to" 字符串
    return frozenset(f"{f}->{t}" for f, t in result)

# ══════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════


# 编译正则，避免每次调用都重新编译
_STOP_WORDS_PATTERN = re.compile(r"(?:后才能|准备好|已完成|完成|完毕|就绪)")


def _short_label(
    text: Optional[str],
    max_width: int = 24,  # ← 改名：现在是"显示宽度"而非"字符数"
    ellipsis: str = "…",
) -> str:
    if not text or not str(text).strip():
        return ""

    cleaned = _STOP_WORDS_PATTERN.sub("", str(text)).strip()
    if not cleaned:
        return ""

    # ✅ 使用显示宽度而非字符数
    if _label_length(cleaned) <= max_width:
        return cleaned

    # ✅ 按显示宽度安全截断
    truncated = ""
    current_width = 0
    ellipsis_width = _label_length(ellipsis)
    available = max_width - ellipsis_width

    for ch in cleaned:
        ch_width = _char_width(ch)  # 单字符版本，避免重复调用 wcswidth
        if current_width + ch_width > available:
            break
        truncated += ch
        current_width += ch_width

    return truncated + ellipsis


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


def _is_loop_edge(edge: Dict) -> bool:
    """
    判断是否为自环边。

    - 对 None/空值返回 False（无效边不是自环）
    - 去除首尾空白后比较
    - 记录异常格式便于排查
    """
    src = edge.get("from")
    tgt = edge.get("to")

    # ✅ 无效边不应被视为自环
    if src is None or tgt is None:
        return False

    src_clean = str(src).strip()
    tgt_clean = str(tgt).strip()

    # ✅ 空标识符不构成有效自环
    if not src_clean or not tgt_clean:
        logger.warning(
            "Edge with empty endpoint detected: from=%r, to=%r",
            src, tgt
        )
        return False

    return src_clean == tgt_clean


def _get_edge_type(edge: dict) -> str:
    """
    提取并验证边的类型。

    - 未知类型降级为 DEFAULT_EDGE_TYPE 并记录警告
    - None/空值安全处理
    - 大小写不敏感匹配
    """
    raw = edge.get("type")

    # ✅ 显式处理 None，避免 str(None) 的隐式转换
    if raw is None:
        return DEFAULT_EDGE_TYPE

    normalized = str(raw).strip().lower()

    if normalized in _VALID_EDGE_TYPES:
        return normalized

    # ✅ 关键：静默降级改为可观测降级
    # 仅在非空时告警，空值视为"未指定"而非"错误"
    if normalized:
        logger.warning(
            "Unknown edge type %r (from=%s, to=%s), falling back to %s",
            raw, edge.get("from"), edge.get("to"), DEFAULT_EDGE_TYPE
        )

    return DEFAULT_EDGE_TYPE


def _detect_cycles(edges: List[Dict]) -> Set[str]:
    """
    检测哪些边属于循环/回边（形成环的边）。
    使用迭代版三色 DFS 避免 RecursionError，并支持多重边检测。
    """
    # ✅ 1. 构建邻接表与节点顺序
    # 使用 dict 替代 list 维护节点顺序，实现 O(1) 查重，整体构建 O(E)
    # adj[u] = [(v, edge_index), ...]
    adj: Dict[str, List[Tuple[str, int]]] = {}
    nodes_order_dict: Dict[str, None] = {}

    for idx, e in enumerate(edges):
        f = str(e.get("from", ""))
        t = str(e.get("to", ""))

        if not f or not t:
            continue  # 容忍脏数据，跳过无效边

        if f not in adj:
            adj[f] = []
        # ✅ 记录目标节点和原始边索引，支持多重边（平行边）
        adj[f].append((t, idx))

        nodes_order_dict[f] = None
        nodes_order_dict[t] = None

    # ✅ 2. 迭代版三色 DFS (避免 RecursionError)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes_order_dict}
    cycle_edge_indices = set()  # 收集回边的原始索引

    for start_node in nodes_order_dict:
        if color[start_node] != WHITE:
            continue

        # 显式栈：存储 (当前节点, 邻接表迭代器)
        # 使用迭代器可以避免每次恢复现场时重新遍历已访问的邻居
        stack = [(start_node, iter(adj.get(start_node, [])))]
        color[start_node] = GRAY

        while stack:
            node, neighbors_iter = stack[-1]
            try:
                nxt, edge_idx = next(neighbors_iter)

                if color.get(nxt) == GRAY:
                    # 遇到灰色节点，说明存在环，当前边是回边
                    cycle_edge_indices.add(edge_idx)
                elif color.get(nxt) == WHITE:
                    # 遇到白色节点，深入探索
                    color[nxt] = GRAY
                    stack.append((nxt, iter(adj.get(nxt, []))))
            except StopIteration:
                # 当前节点的所有邻居已探索完毕，回溯
                stack.pop()
                color[node] = BLACK

    # ✅ 3. 格式化输出
    # 建议调用方改用 Set[int] (边索引) 或 Set[Tuple[str, str]]。
    # 若必须保持 Set[str] 兼容，使用不可见字符或 JSON 确保安全
    cycle_edges = set()
    for idx in cycle_edge_indices:
        e = edges[idx]
        f = str(e.get("from", ""))
        t = str(e.get("to", ""))
        cycle_edges.add((f, t))
    return cycle_edges

def _status_icon(status_code: int) -> str:
    """
    状态码 → emoji 图标。
    默认样式从 NODE_STYLES 中按语义查找，不依赖列表位置。
    """
    # ✅ 类型归一化：容忍上游传入字符串
    try:
        normalized_code = int(status_code)
    except (ValueError, TypeError):
        normalized_code = None

    # ✅ 从 config 中语义化获取默认样式
    # 根据你的 config 生成逻辑，选择以下其中一种方式替换 NODE_STYLES[0]：
    #   方式A: 有显式标记 → next((s for s in NODE_STYLES if s.get("is_default")), None)
    #   方式B: 约定 status_code=0 为默认 → NODE_STYLES.get(0)
    #   方式C: 若确实是 list 且第一项约定为默认 → 保留 NODE_STYLES[0] 但加注释说明契约
    default_style = NODE_STYLES[0]  # ← 请根据实际 config 契约替换此行

    style = NODE_STYLES.get(normalized_code, default_style)
    return style.get("icon", "❓")


def _node_state_class(
    nid: str,
    status: int,
    node_states: Dict[str, str],
    in_degree: Dict[str, int],
    done_nodes: set[str],
    edges: List[Dict],
) -> str:
    """
    节点状态 → CSS class
    - blocked 判断只考虑 hard 边
    - skipped 不再自动传播
    - status==4 映射为 timeout（与 flowchart_pro.py 对齐）
    """
    # ── 优先使用前端用户手动设置的状态 ──
    user_state = node_states.get(nid)
    if user_state == "done":
        return "state-done"
    elif user_state == "running":
        return "state-running"
    elif user_state == "failed":
        return "state-failed"
    elif user_state == "skipped":
        return "state-skipped"
    elif user_state == "timeout":       # ← NEW
        return "state-timeout"

    # ── 其次使用后端数据库状态码 ──
    if status == 2:
        return "state-done"
    elif status == 1:
        return "state-running"
    elif status == 3:
        return "state-failed"
    elif status == 4:
        return "state-timeout"          # ← FIXED: 原来是 state-skipped

    # ── 默认：根据 hard 入边判断 ready / blocked ──
    my_in_edges = [e for e in edges if str(e["to"]) == nid]
    hard_in_edges = [
        e for e in my_in_edges if _get_edge_type(e) == EDGE_TYPE_HARD
    ]

    if not hard_in_edges:
        return "state-ready"

    all_hard_done = all(
        str(e["from"]) in done_nodes for e in hard_in_edges
    )
    return "state-ready" if all_hard_done else "state-blocked"


# 修改 _edge_class 定义，让它从 edge 自身提取所需信息
def _edge_class(edge: dict, node_states: Dict[str, str], is_cycle: bool) -> str:
    """
    根据边类型、节点状态、是否为循环边生成 CSS class。
    """
    t = _get_edge_type(edge)
    base = EDGE_TYPE_CSS.get(t, EDGE_TYPE_CSS[DEFAULT_EDGE_TYPE])
    classes = [base]

    # 如果边是循环边，额外添加 class
    if is_cycle:
        classes.append("edge-cycle")

    # 如果源节点或目标节点状态为 done/skipped 等，可添加额外 class（可选）
    # 这里可以根据需求扩充
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
    支持循环边、动态边距、长标签截断、边类型。
    动态值统一放 data，样式映射放样式表，不用 style bypass。
    """
    if node_states is None:
        node_states = {}

    elements: List[Dict] = []

    # 计算入度
    in_degree: Dict[str, int] = {}
    for e in edges:
        t = str(e["to"])
        in_degree[t] = in_degree.get(t, 0) + 1

    # 已完成节点集合
    done_nodes: set[str] = {
        nid for nid, st in node_states.items() if st == "done"
    }
    for n in nodes:
        if int(n.get("status", 0)) == 2:
            done_nodes.add(str(n["id"]))

    # 检测循环边
    cycle_edge_ids = _detect_cycles_cached(edges)

    # ── 节点 ──────────────────────────────
    for node in nodes:
        nid = str(node["id"])
        status = int(node.get("status", 0))
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
            nid, status, node_states, in_degree, done_nodes, edges
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
                "preconditions_text": "；".join(str(p) for p in pre) if pre else "无",
                "postconditions": [str(p) for p in post],
                "postconditions_text": "；".join(str(p) for p in post) if post else "无",
                "retry_count": int(node.get("retry_count", 0)),
                "retry_policy": str(meta.get("retry_policy", "失败后重试，最多3次")),
                "width": node_width,
            },
            "classes": cls,
        })

    # ── 边 ──────────────────────────────
    for edge in edges:
        f = str(edge["from"])
        t = str(edge["to"])
        eid = f"{f}->{t}"
        raw_label = str(edge.get("label", ""))
        short = _short_label(raw_label, max_width=16)   # ← 改为 max_width

        is_cycle = eid in cycle_edge_ids or _is_loop_edge(edge)
        # 直接用 edge 对象调用
        cls = _edge_class(edge, node_states, is_cycle)

        # 如果自环，再追加 edge-loop
        if _is_loop_edge(edge):
            cls += " edge-loop"        
        label_display_len = _label_length(raw_label)
        edge_text_margin = max(10, min(40, label_display_len * 2))

        elements.append({
            "data": {
                "id": eid,
                "source": f,
                "target": t,
                "label": short,
                "full_label": raw_label,
                "label_length": label_display_len,
                "text_margin_y": edge_text_margin,
                "edge_type": edge_type,  # ← NEW: 边类型放入 data，供前端交互使用
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
        st = int(n.get("status", 0))
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
    """生成节点详情的 Markdown 文本（供 dcc.Markdown 渲染）"""
    if not node_data:
        return "👆 **悬停或点击节点查看详细信息**"

    status = node_data.get("status", 0)
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
    """生成历史任务详情的 Markdown 文本"""
    lines = [
        f"## 📋 任务 `{task_id[-8:]}`",
        f"**总节点数**: {len(nodes)}",
        "",
        "### 节点列表",
        "",
        "| 状态 | ID | 名称 |",
        "|---|---|---|",
    ]
    icon_map = {0: "⏳", 1: "🔄", 2: "✅", 3: "❌", 4: "⏭️"}
    for n in nodes:
        ic = icon_map.get(n.get("status", 0), "?")
        lines.append(f"| {ic} | #{n['id']} | {n.get('name', '')} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("点击右侧流程图中的节点可查看各节点详情。")

    return "\n".join(lines)

