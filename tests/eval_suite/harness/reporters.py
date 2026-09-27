"""
报告生成器：Markdown / HTML 格式
"""

from typing import Dict, List, Any
import json
import datetime


class Reporters:

    @staticmethod
    def markdown_report(summary: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
        lines = [
            "# Evaluation Report",
            f"Generated: {datetime.datetime.utcnow().isoformat()}",
            "",
            "## Summary",
            f"- Total Cases: {summary.get('total', 0)}",
            f"- Success Rate: {summary.get('success_rate', 0):.2%}",
            f"- Average Accuracy: {summary.get('avg_accuracy', 0):.2%}",
            f"- Average Relevance: {summary.get('avg_relevance', 0):.2%}",
            f"- Average Latency: {summary.get('avg_latency', 0):.2f}s",
            f"- Acyclic Rate: {summary.get('acyclic_rate', 0):.2%}",
            f"- Plan Generation Rate: {summary.get('plan_generation_rate', 0):.2%}",
            "",
            "### By Category",
        ]
        for cat, data in summary.get("by_category", {}).items():
            metrics = data.get("metrics", {})
            lines.append(f"- **{cat}** ({data['count']} cases): accuracy={metrics.get('accuracy', 0):.2%}, success={metrics.get('success', 0):.2%}")

        lines.append("")
        lines.append("## Detailed Results")
        for r in results:
            lines.append(f"### Case {r.get('case_id')} ({r.get('case_category')})")
            lines.append(f"- Input: {r.get('input')}")
            lines.append(f"- Output: {r.get('direct_response', 'N/A')}")
            lines.append(f"- Success: {r.get('success')}")
            lines.append(f"- Elapsed: {r.get('elapsed', 0):.2f}s")
            metrics = r.get("metrics", {})
            lines.append(f"- Metrics: accuracy={metrics.get('accuracy', 0):.2%}, relevance={metrics.get('relevance', 0):.2%}, plan_generated={metrics.get('plan_generated', 0):.2%}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def html_report(summary: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
        # 简单HTML生成，可直接嵌入浏览器
        md_content = Reporters.markdown_report(summary, results)
        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>Evaluation Report</title>
        <style>body{{font-family: sans-serif; margin:40px;}} pre{{background:#f4f4f4; padding:10px;}}</style>
        </head>
        <body><pre>{md_content}</pre></body>
        </html>
        """
