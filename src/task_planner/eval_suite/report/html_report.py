"""html_report.py — HTML 评估报告输出。"""
import html as H
from datetime import datetime
from pathlib import Path

from ..types import CaseResult


def render_html_report(results: list[CaseResult], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        det = r.deterministic.overall
        jury = r.jury.aggregated_overall if r.jury else 0.0
        agree = r.jury.agreement if r.jury else 0.0
        cls = ' class="err"' if r.error else ''
        rows.append(f"""<tr{cls}>
  <td>{H.escape(r.case_id)}</td>
  <td>{r.difficulty.value}</td>
  <td>{det:.2f}</td>
  <td>{jury:.2f}</td>
  <td>{agree:.2f}</td>
  <td>{r.duration_sec:.1f}s</td>
  <td>{H.escape(str(r.error or ''))[:80]}</td>
</tr>""")

    output.write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Eval Report</title>
<style>
body{{font-family:system-ui;margin:40px;background:#f8fafc}}
table{{width:100%;border-collapse:collapse;background:white;box-shadow:0 2px 8px rgba(0,0,0,.06);border-radius:8px;overflow:hidden}}
th{{background:#1e293b;color:white;padding:12px;text-align:left}}
td{{padding:10px 12px;border-bottom:1px solid #e2e8f0}}
tr.err td{{background:#fef2f2}}
.meta{{color:#64748b;font-size:13px;margin-bottom:20px}}
</style></head><body>
<h1>🧪 Eval Suite Report</h1>
<p class="meta">生成时间：{datetime.now().isoformat()} | 用例数：{len(results)}</p>
<table><thead><tr>
<th>Case</th><th>难度</th><th>确定性</th><th>评委</th><th>一致性</th><th>耗时</th><th>错误</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>""", encoding="utf-8")
    return output


__all__ = ["render_html_report"]
