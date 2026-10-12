"""sheet.py — HIL 待审清单生成。"""
import json
from datetime import datetime, timezone
from pathlib import Path
from ..types import CaseResult


def generate_review_sheet(results: list[CaseResult], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    items = []
    for r in results:
        items.append({
            "case_id": r.case_id,
            "user_input": r.user_input,
            "difficulty": r.difficulty.value,
            "error": r.error,
            "deterministic_score": r.deterministic.overall,
            "jury_score": r.jury.aggregated_overall if r.jury else None,
            "consensus": r.jury.agreement if r.jury else None,
            "meta_review": r.jury.meta_review if r.jury else "",
            "human_review": {
                "status": "pending",
                "human_score": None,
                "human_comment": "",
                "revised_suggestion": "",
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    output.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


__all__ = ["generate_review_sheet"]
