"""deterministic.py — 确定性评分（无需 LLM）"""

from ..types import AgentTrace, DeterministicScores, GoldenCase


def score_deterministic(trace: AgentTrace, golden: GoldenCase) -> DeterministicScores:
    s = DeterministicScores()
    if trace.error:
        return s

    exp_i = golden.expected_intent
    s = DeterministicScores(
        intent_match=1.0
        if (
            trace.intent.get("needs_planning") == exp_i.get("needs_planning")
            and trace.intent.get("category") == exp_i.get("category")
        )
        else 0.0,
        node_count_accuracy=_count_acc(
            len(golden.expected_plan.get("nodes", [])),
            len(trace.plan.get("nodes", [])),
        ),
        edge_count_accuracy=_count_acc(
            len(golden.expected_plan.get("edges", [])),
            len(trace.plan.get("edges", [])),
        ),
        structure_valid=_validate_structure(trace.plan),
        node_name_overlap=_jaccard(
            {n["name"] for n in golden.expected_plan.get("nodes", []) if n.get("name")},
            {n.get("name", "") for n in trace.plan.get("nodes", [])},
        ),
    )
    return s


def _count_acc(expected: int, actual: int) -> float:
    if expected <= 0:
        return 1.0 if actual == 0 else 0.0
    return round(1.0 - min(abs(actual - expected) / expected, 1.0), 3)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return round(len(a & b) / len(a | b), 3) if (a | b) else 0.0


def _validate_structure(plan: dict) -> bool:
    nodes = plan.get("nodes", [])
    edges = plan.get("edges", [])
    if not nodes:
        return False
    ids = [n.get("id") for n in nodes]
    if any(i is None or not isinstance(i, int) for i in ids):
        return False
    if set(ids) != set(range(1, len(ids) + 1)):
        return False
    id_set = set(ids)
    adj = {i: [] for i in ids}
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f not in id_set or t not in id_set or f == t:
            return False
        adj[f].append(t)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(ids, WHITE)
    for start in ids:
        if color[start] != WHITE:
            continue
        stack = [(start, 0)]
        color[start] = GRAY
        while stack:
            node, idx = stack[-1]
            if idx < len(adj[node]):
                nxt = adj[node][idx]
                stack[-1] = (node, idx + 1)
                if color[nxt] == GRAY:
                    return False
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, 0))
            else:
                stack.pop()
                color[node] = BLACK
    return True


__all__ = ["score_deterministic"]
