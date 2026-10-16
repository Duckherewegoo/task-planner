"""json_report.py — JSON 评估报告输出。"""
import json
from pathlib import Path

from ..types import CaseResult


def write_json_report(results: list[CaseResult], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    data = [_serialize(r) for r in results]
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _serialize(r: CaseResult) -> dict:
    out = {
        "case_id": r.case_id,
        "user_input": r.user_input,
        "difficulty": r.difficulty.value,
        "duration_sec": r.duration_sec,
        "error": r.error,
        "mock_mode": r.mock_mode,
        "deterministic": {
            "overall": r.deterministic.overall,
            "intent_match": r.deterministic.intent_match,
            "node_count_accuracy": r.deterministic.node_count_accuracy,
            "edge_count_accuracy": r.deterministic.edge_count_accuracy,
            "structure_valid": r.deterministic.structure_valid,
            "node_name_overlap": r.deterministic.node_name_overlap,
        },
    }
    if r.jury:
        out["jury"] = {
            "aggregated_overall": r.jury.aggregated_overall,
            "agreement": r.jury.agreement,
            "confidence_lower": r.jury.confidence_lower,
            "confidence_upper": r.jury.confidence_upper,
            "strategy": r.jury.strategy.value,
            "dimensions": r.jury.aggregated_dimensions,
            "meta_review": r.jury.meta_review,
            "judge_count": len(r.jury.judge_scores),
        }
    if r.human_review:
        out["human_review"] = {
            "status": r.human_review.status.value,
            "human_score": r.human_review.human_score,
            "human_comment": r.human_review.human_comment,
        }
    return out


__all__ = ["write_json_report"]
