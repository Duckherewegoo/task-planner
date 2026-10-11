"""
dash_app.py — 独立的 HIL 人工审查 Dash 前端。

不 import main UI 的任何模块。只读 eval_suite 生成的 JSON。
启动：python -m task_planner.eval_suite.hil.dash_app
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import dash
from dash import Input, Output, State, callback, dcc, html

from task_planner.eval_suite.types import CaseStatus


def build_layout(review_path: Path) -> html.Div:
    """构建 HIL 审查页面。"""
    items = []
    if review_path.exists():
        items = json.loads(review_path.read_text(encoding="utf-8"))

    rows = []
    for i, item in enumerate(items):
        rows.append(html.Div([
            html.H3(f"{item['case_id']} — {item.get('difficulty', '-')}"),
            html.P(f"用户需求: {item.get('user_input', '')}"),
            html.Div([
                html.Span(f"确定性: {item.get('deterministic_score', 0):.2f}"),
                html.Span(f"  评委: {item.get('jury_score', 0):.2f}"),
                html.Span(f"  一致性: {item.get('consensus', 0):.2f}"),
            ], style={"color": "#64748b", "fontSize": "13px"}),
            html.Pre(
                item.get("meta_review", ""),
                style={"background": "#f8fafc", "padding": "10px",
                       "borderRadius": "6px", "fontSize": "12px"},
            ),
            html.Div([
                dcc.RadioItems(
                    id={"type": "hil-status", "index": i},
                    options=[
                        {"label": "通过", "value": CaseStatus.APPROVED.value},
                        {"label": "拒绝", "value": CaseStatus.REJECTED.value},
                        {"label": "标记", "value": CaseStatus.FLAGGED.value},
                    ],
                    value=item.get("human_review", {}).get("status", "pending"),
                    inline=True,
                ),
                dcc.Input(
                    id={"type": "hil-score", "index": i},
                    type="number", min=0, max=10, step=0.5,
                    placeholder="人工评分 0-10",
                ),
                dcc.Textarea(
                    id={"type": "hil-comment", "index": i},
                    placeholder="人工评语...",
                    style={"width": "100%", "height": "60px"},
                ),
            ], style={"marginTop": "12px"}),
        ], style={
            "padding": "16px", "border": "1px solid #e2e8f0",
            "borderRadius": "10px", "marginBottom": "16px",
            "background": "white",
        }))

    return html.Div([
        html.H1("🔍 Eval Suite — HIL 人工审查"),
        html.P("在下面填写人工评分和评语，点击保存导出结果。"),
        html.Div(id="hil-items", children=rows),
        html.Button("💾 保存审查结果", id="hil-save-btn", n_clicks=0),
        html.Div(id="hil-save-status"),
        dcc.Store(id="hil-review-path", data=str(review_path)),
    ], style={"maxWidth": "900px", "margin": "40px auto", "padding": "24px"})


def main(review_path: str = "eval_reports/hil_review.json"):
    app = dash.Dash(__name__, title="Eval HIL")
    app.layout = build_layout(Path(review_path))

    @callback(
        Output("hil-save-status", "children"),
        Input("hil-save-btn", "n_clicks"),
        State({"type": "hil-status", "index": dash.ALL}, "value"),
        State({"type": "hil-score", "index": dash.ALL}, "value"),
        State({"type": "hil-comment", "index": dash.ALL}, "value"),
        State("hil-review-path", "data"),
        prevent_initial_call=True,
    )
    def _save(n_clicks, statuses, scores, comments, path):
        try:
            path = Path(path)
            if not path.exists():
                return f"❌ 文件不存在: {path}"
            items = json.loads(path.read_text(encoding="utf-8"))
            if not (len(items) == len(statuses) == len(scores) == len(comments)):
                return "❌ 条目数不一致，请刷新页面"
            for i, item in enumerate(items):
                item["human_review"] = {
                    "status": statuses[i],
                    "human_score": scores[i],
                    "human_comment": comments[i],
                }
            path.write_text(
                json.dumps(items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return f"✅ 已保存 {len(items)} 条审查结果"
        except Exception as e:
            return f"❌ 保存失败: {type(e).__name__}: {e}"

    # 从环境变量读监听地址与端口，便于容器化 / 远程审查
    host = os.environ.get("HIL_HOST", "127.0.0.1")
    port = int(os.environ.get("HIL_PORT", "8050"))
    debug = os.environ.get("HIL_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
