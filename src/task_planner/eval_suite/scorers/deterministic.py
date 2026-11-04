"""deterministic.py — 确定性评分（无需 LLM）"""

from ..types import AgentTrace, DeterministicScores, GoldenCase
from ..golden.validator import validate_golden_plan

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
    is_valid, _ = validate_golden_plan(plan)
    return is_valid

__all__ = ["score_deterministic"]
