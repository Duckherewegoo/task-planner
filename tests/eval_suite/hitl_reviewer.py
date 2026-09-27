"""
人工审查 (Human-in-the-Loop) 工具
标记需要审查的案例，提供交互式审查界面（CLI 或 Web）
"""

import json
from typing import List, Dict, Any
from pathlib import Path
from datetime import datetime
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("eval.hitl")


class HITLReviewer:
    """
    人工审查管理器
    """

    def __init__(self, review_dir: str = "reviews"):
        self.review_dir = Path(review_dir)
        self.review_dir.mkdir(exist_ok=True)

    def flag_for_review(self, results: List[Dict[str, Any]], threshold: float = 0.5) -> List[Dict[str, Any]]:
        """
        筛选需要审查的结果
        """
        flagged = []
        for r in results:
            metrics = r.get("metrics", {})
            if (
                metrics.get("accuracy", 1.0) < threshold
                or metrics.get("success", 1.0) < 0.5
                or r.get("hitl_required", False)
            ):
                flagged.append(r)
        logger.info(f"Flagged {len(flagged)} cases for review out of {len(results)}")
        return flagged

    def save_review_requests(self, flagged: List[Dict[str, Any]]) -> str:
        """
        保存审查请求到文件
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_path = self.review_dir / f"review_requests_{timestamp}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(flagged, f, indent=2, default=str)
        logger.info(f"Review requests saved to {file_path}")
        return str(file_path)

    def load_review_results(self, file_path: str) -> List[Dict[str, Any]]:
        """加载已完成的审查结果"""
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def interactive_review(self, flagged: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        CLI 交互式审查
        逐个显示案例，让用户评分 (1-5) 和评论
        """
        reviewed = []
        for idx, item in enumerate(flagged):
            print(f"\n===== Review {idx+1}/{len(flagged)} =====")
            print(f"Input: {item.get('input', 'N/A')}")
            print(f"Output: {item.get('direct_response', 'N/A')}")
            print(f"Nodes: {len(item.get('nodes', []))}")
            print(f"Elapsed: {item.get('elapsed', 0):.2f}s")
            print(f"Metrics: {item.get('metrics', {})}")

            score = input("Score (1-5, 5=perfect): ")
            comment = input("Comment: ")
            reviewed.append({
                **item,
                "human_score": int(score) if score.isdigit() else None,
                "human_comment": comment,
                "reviewed_at": datetime.utcnow().isoformat(),
            })

        return reviewed

    def review_summary(self, reviewed: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成审查摘要"""
        scores = [r.get("human_score") for r in reviewed if r.get("human_score") is not None]
        return {
            "total_reviewed": len(reviewed),
            "avg_human_score": sum(scores) / len(scores) if scores else 0,
            "scores": scores,
            "comments": [r.get("human_comment") for r in reviewed if r.get("human_comment")],
        }
