"""
validator.py — 程序化验证黄金标准 DAG 的结构有效性。
不依赖 LLM，纯确定性检查。
"""

from typing import Any


def validate_golden_plan(plan: dict[str, Any]) -> tuple[bool, str]:
    """
    验证 DAG 结构有效性。

    Returns:
        (is_valid, reason)
    """
    nodes = plan.get("nodes", [])
    edges = plan.get("edges", [])

    if not nodes:
        return False, "nodes 为空"

    # 1. ID 连续且唯一、且必须是 int
    ids = [n.get("id") for n in nodes]
    if any(i is None for i in ids):
        return False, "存在无 id 的节点"
    for i in ids:
        if not isinstance(i, int) or isinstance(i, bool):
            return False, f"id 必须是 int，收到 {i!r}"
    if len(ids) != len(set(ids)):
        return False, "id 重复"
    if set(ids) != set(range(1, len(ids) + 1)):
        return False, f"id 不连续: {sorted(ids)}"

    # 2. 节点必须有 name
    for n in nodes:
        if not n.get("name"):
            return False, f"节点 #{n.get('id')} 缺 name"

    # 3. 边引用的节点必须存在
    id_set = set(ids)
    for e in edges:
        f = e.get("from")
        t = e.get("to")
        if f not in id_set:
            return False, f"边 from={f} 引用不存在的节点"
        if t not in id_set:
            return False, f"边 to={t} 引用不存在的节点"
        if f == t:
            return False, f"自环: {f}->{t}"

    # 3.5 重复边检测
    seen_edges: set[tuple[int, int]] = set()
    for e in edges:
        key = (e.get("from"), e.get("to"))
        if key in seen_edges:
            return False, f"重复边: {key}"
        seen_edges.add(key)

    # 4. 无环检测（迭代 DFS）
    adj: dict[int, list[int]] = {i: [] for i in ids}
    for e in edges:
        adj[e["from"]].append(e["to"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(ids, WHITE)

    for start in ids:
        if color[start] != WHITE:
            continue
        stack: list[tuple[int, int]] = [(start, 0)]
        color[start] = GRAY
        while stack:
            node, idx = stack[-1]
            if idx < len(adj[node]):
                nxt = adj[node][idx]
                stack[-1] = (node, idx + 1)
                if color[nxt] == GRAY:
                    return False, f"检测到环: 经过节点 {nxt}"
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, 0))
            else:
                stack.pop()
                color[node] = BLACK

    return True, "OK"
