"""
报告生成器：Markdown / HTML 格式

Changelog:
  ✅ P0-1：datetime.utcnow() → datetime.now(timezone.utc)，兼容 Python 3.12+
  ✅ P0-2：html_report 用 html.escape() 转义 Markdown 内容，
           防止文本里的 < > & 破坏 HTML 结构（安全 + 功能双重修复）
  ✅ P1-1：所有格式化字段加 None 安全处理，避免 NoneType.__format__ 崩溃
  ✅ P1-2：data['count'] 改为 data.get('count', 0)
  ✅ P2-1：移除未使用的 import json
  ✅ P2-2：日志/报告里的时间戳统一带时区
"""

import datetime
import html
from typing import Any, Dict, List


class Reporters:

    @staticmethod
    def markdown_report(
        summary: Dict[str, Any],
        results: List[Dict[str, Any]],
    ) -> str:
        """生成 Markdown 格式报告"""

        # ✅ P0-1 修复：带时区的 UTC 时间
        generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # ✅ P1-1 修复：安全的百分比格式化
        def pct(v: Any) -> str:
            try:
                return f"{float(v or 0):.2%}"
            except (TypeError, ValueError):
                return "N/A"

        def sec(v: Any) -> str:
            try:
                return f"{float(v or 0):.2f}s"
            except (TypeError, ValueError):
                return "N/A"

        lines = [
            "# Evaluation Report",
            f"Generated: {generated_at}",
            "",
            "## Summary",
            f"- Total Cases: {summary.get('total', 0)}",
            f"- Success Rate: {pct(summary.get('success_rate'))}",
            f"- Average Accuracy: {pct(summary.get('avg_accuracy'))}",
            f"- Average Relevance: {pct(summary.get('avg_relevance'))}",
            f"- Average Latency: {sec(summary.get('avg_latency'))}",
            f"- Acyclic Rate: {pct(summary.get('acyclic_rate'))}",
            f"- Plan Generation Rate: {pct(summary.get('plan_generation_rate'))}",
            "",
            "### By Category",
        ]

        for cat, data in summary.get("by_category", {}).items():
            metrics = data.get("metrics", {}) or {}
            count = data.get("count", 0)   # ✅ P1-2 修复：用 .get
            lines.append(
                f"- **{cat}** ({count} cases): "
                f"accuracy={pct(metrics.get('accuracy'))}, "
                f"success={pct(metrics.get('success'))}"
            )

        # 可选：复杂度分组
        by_complexity = summary.get("by_complexity", {})
        if by_complexity:
            lines.append("")
            lines.append("### By Complexity")
            for comp, data in by_complexity.items():
                metrics = data.get("metrics", {}) or {}
                count = data.get("count", 0)
                lines.append(
                    f"- **{comp}** ({count} cases): "
                    f"accuracy={pct(metrics.get('accuracy'))}"
                )

        lines.append("")
        lines.append("## Detailed Results")

        for r in results:
            metrics = r.get("metrics", {}) or {}
            lines.append(f"### Case {r.get('case_id')} ({r.get('case_category')})")
            lines.append(f"- Input: {r.get('input')}")
            lines.append(f"- Output: {r.get('direct_response') or 'N/A'}")
            lines.append(f"- Success: {r.get('success')}")
            lines.append(f"- Elapsed: {sec(r.get('elapsed'))}")
            lines.append(
                f"- Metrics: "
                f"accuracy={pct(metrics.get('accuracy'))}, "
                f"relevance={pct(metrics.get('relevance'))}, "
                f"plan_generated={pct(metrics.get('plan_generated'))}"
            )
            if r.get("error"):
                lines.append(f"- Error: `{r['error']}`")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def html_report(
        summary: Dict[str, Any],
        results: List[Dict[str, Any]],
    ) -> str:
        """
        生成 HTML 格式报告。

        ✅ P0-2 修复：原实现直接 f"<pre>{md_content}</pre>"，
           Markdown 里的 `<` `>` `&` 会破坏 HTML 结构：
             - 用户输入含 `<script>alert(1)</script>` → XSS 风险
             - 输入含 `a < b` → HTML 解析器直接吞掉后半段
           现在用 html.escape() 转义，保证渲染正确且安全。
        """
        md_content = Reporters.markdown_report(summary, results)
        escaped = html.escape(md_content)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>Evaluation Report</title>
    <style>
        body {{
            font-family: -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif;
            margin: 40px;
            line-height: 1.6;
            color: #1e293b;
            max-width: 1200px;
        }}
        h1 {{ border-bottom: 2px solid #3b82f6; padding-bottom: 8px; }}
        pre {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 16px;
            overflow-x: auto;
            white-space: pre-wrap;
            word-wrap: break-word;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <pre>{escaped}</pre>
</body>
</html>
"""
