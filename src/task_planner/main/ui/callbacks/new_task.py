"""new_task.py — 新建任务的回调"""
from __future__ import annotations

import time
import uuid
from typing import Any

from dash import Input, Output, Patch, State, callback, ctx, dcc, html, no_update

from task_planner.core.graph.nodes import sanitize_node_for_user
from task_planner.core.graph.state import NodeStatus
from task_planner.infrastructure.constants import (
    HINT_TEMPLATES,
    NODE_STATUS_CODE_MAP,   # noqa: F401
    STATE_COLORS,
    STATE_LABELS,
)
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.ui_styles import (
    BAR_DONE,
    BAR_ERROR,
    BAR_LOADING,
    BASE_BTN_STYLE,
    EMPTY_BAR_MINI_STYLE,
    EMPTY_BAR_STYLE,
    EMPTY_DAG,
    EMPTY_STATES,
    HINT_STYLES,
    MARKDOWN_PRE_STYLE,
)
from task_planner.services.agent import run_task_stream
from task_planner.services.stream_manager import (
    cancel_stream,
    get_stream_state,
    start_stream,
)
from task_planner.utils.cytoscape_adapter import build_detail_markdown

from ..constants import cytoscape_cached
from ..data_ops import auto_unlock_downstream, safe_elapsed


def register() -> None:
    # ═══════════════════════════════════════════════════════════
    #  纯前端状态回调
    # ═══════════════════════════════════════════════════════════
    @callback(
        Output("node-action-hint", "children"),
        Output("node-action-hint", "style"),
        Input("flowchart", "tapNodeData"),
        State("node-states-store", "data"),
        State("dag-store", "data"),
    )
    def on_node_hint(node_data, node_states, dag):
        if not dag or not dag.get("nodes"):
            return (
                HINT_TEMPLATES.get("loading", "⏳ 加载中..."),
                HINT_STYLES.get("loading", {"color": "#94a3b8", "fontSize": "14px"}),
            )
        if not node_data:
            return HINT_TEMPLATES["ready"], HINT_STYLES["ready"]
        nid = str(node_data.get("id", ""))
        raw_st = (node_states or {}).get(nid, NodeStatus.PENDING)
        try:
            status = NodeStatus(raw_st)
        except (ValueError, KeyError):
            status = NodeStatus.PENDING
        text = HINT_TEMPLATES.get(status.value, HINT_TEMPLATES["pending"]).format(nid=nid)
        style = HINT_STYLES.get(status.value, HINT_STYLES["pending"])
        return text, style

    @callback(
        Output("flowchart", "className"),
        Input("flowchart", "mouseoverNodeData"),
        Input("flowchart", "mouseoutNodeData"),
        prevent_initial_call=True,
    )
    def on_node_hover(over_data, out_data):
        triggered = ctx.triggered_id
        if triggered == "flowchart" and over_data:
            return Patch({"addClasses": f"#node-{over_data['id']}.node-hover"})
        return no_update

    @callback(
        Output("node-detail-panel", "children"),
        Output("selected-node-store", "data"),
        Input("flowchart", "tapNodeData"),
        State("dag-store", "data"),
        State("node-states-store", "data"),
    )
    def on_tap_node(node_data, dag, node_states):
        if not node_data or not node_data.get("id"):
            return (
                dcc.Markdown(
                    "👆 **点击流程图中的节点查看详情并操作**",
                    style={"fontSize": "14px", "color": "#94a3b8"},
                ),
                no_update,
            )
        nid = str(node_data["id"])
        st = str((node_states or {}).get(nid, "pending"))
        md_text = build_detail_markdown(dict(node_data))
        current_label = STATE_LABELS.get(st, st)

        def make_btn(label, btn_id, state_key):
            bg, color = STATE_COLORS.get(state_key, ("#e2e8f0", "#475569"))
            is_active = st == state_key
            style = {
                **BASE_BTN_STYLE,
                "backgroundColor": bg if is_active else "#e2e8f0",
                "color": color if is_active else "#475569",
                "opacity": "1" if is_active else "0.8",
            }
            return html.Button(label, id=btn_id, n_clicks=0, style=style)

        buttons = [
            make_btn("✅ 完成", "complete-btn", "done"),
            make_btn("⏭️ 跳过", "skip-btn-panel", "skipped"),
            make_btn("❌ 失败", "fail-btn-panel", "failed"),
            html.Button(
                "🔄 重置",
                id="reset-btn-panel", n_clicks=0,
                style={**BASE_BTN_STYLE, "backgroundColor": "#e2e8f0", "color": "#475569"},
            ),
        ]
        panel_children = [
            html.Div(
                f"当前状态: {current_label}",
                style={
                    "fontSize": "13px", "color": "#64748b",
                    "marginBottom": "8px", "fontWeight": "bold",
                },
            ),
            dcc.Markdown(md_text, style={"fontSize": "14px", "lineHeight": "1.8"}),
            html.Div(buttons, style={"marginTop": "12px"}),
        ]
        return html.Div(panel_children), nid

    # ═══════════════════════════════════════════════════════════
    #  异步：提交 / 轮询 / 停止 / 开始执行
    # ═══════════════════════════════════════════════════════════
    @callback(
        Output("thread-id-store", "data"),
        Output("stream-interval", "disabled"),
        Output("stream-active", "data"),
        Output("global-status", "children"),
        Output("flowchart", "elements"),
        Output("submit-btn", "disabled"),
        Output("stop-btn", "disabled"),
        Output("start-exec-btn", "disabled"),
        Output("dag-store", "data"),
        Output("node-states-store", "data"),
        Output("progress-bar-fill", "style"),
        Output("task-start-time", "data"),
        Input("submit-btn", "n_clicks"),
        State("user-input", "value"),
        State("enable-refine", "value"),
        prevent_initial_call=True,
    )
    async def on_submit(n_clicks, user_input, refine_value):
        MAX_INPUT_LENGTH = _hub.dev.MAX_INPUT_LENGTH
        if not user_input or not user_input.strip():
            return ("", True, False, "⚠️ 请输入任务需求", [], False, True, True,
                    EMPTY_DAG, EMPTY_STATES, EMPTY_BAR_STYLE, 0.0)

        cleaned = user_input.strip()
        if len(cleaned) > MAX_INPUT_LENGTH:
            return ("", True, False,
                    f"⚠️ 输入过长（{len(cleaned)}字），请精简至 {MAX_INPUT_LENGTH} 字以内",
                    [], False, True, True,
                    EMPTY_DAG, EMPTY_STATES, EMPTY_BAR_STYLE, 0.0)

        thread_id = str(uuid.uuid4())
        enable_refine = isinstance(refine_value, list) and "refine" in refine_value

        try:
            await start_stream(
                thread_id=thread_id,
                user_input=cleaned,
                enable_refine=enable_refine,
                run_task_stream_fn=run_task_stream,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("start_stream 失败")
            return ("", True, False, f"❌ 任务启动失败: {e}", [],
                    False, True, True,
                    EMPTY_DAG, EMPTY_STATES, EMPTY_BAR_STYLE, 0.0)

        return (
            thread_id, False, True,
            "🚀 任务已提交，正在生成流程图...",
            [], True, False, True,
            EMPTY_DAG, EMPTY_STATES, EMPTY_BAR_STYLE, time.time(),
        )

    # ── 轮询 stream 进度 ──
    @callback(
        Output("flowchart", "elements", allow_duplicate=True),
        Output("global-status", "children", allow_duplicate=True),
        Output("stream-interval", "disabled", allow_duplicate=True),
        Output("submit-btn", "disabled", allow_duplicate=True),
        Output("stop-btn", "disabled", allow_duplicate=True),
        Output("start-exec-btn", "disabled", allow_duplicate=True),
        Output("dag-store", "data", allow_duplicate=True),
        Output("progress-bar-fill", "style", allow_duplicate=True),
        Input("stream-interval", "n_intervals"),
        State("thread-id-store", "data"),
        State("stream-active", "data"),
        State("task-start-time", "data"),
        prevent_initial_call=True,
    )
    async def poll_stream_progress(n_intervals, thread_id, stream_active, task_start_time):
        now = time.time()
        elapsed = safe_elapsed(task_start_time, now)

        if not stream_active or not thread_id:
            return (no_update, no_update, True, False, True, True,
                    no_update, EMPTY_BAR_MINI_STYLE)

        state = await get_stream_state(thread_id)
        if not state:
            return (no_update, f"⚠️ 任务状态丢失 (⏱️ {elapsed}s)",
                    True, False, True, True,
                    no_update, EMPTY_BAR_MINI_STYLE)

        latest = await state.get_latest()
        direct_response = latest.get("direct_response", "")
        if direct_response:
            msg = f"💬 {direct_response}\n\n⏱️ 总耗时 {elapsed}s"
            return ([], dcc.Markdown(msg, style=MARKDOWN_PRE_STYLE),
                    True, False, True, True, no_update, EMPTY_BAR_STYLE)

        nodes = latest.get("nodes") or []
        edges = latest.get("edges") or []
        node_states = latest.get("node_states") or {}
        dag = {"nodes": nodes, "edges": edges}
        elements = cytoscape_cached(nodes, edges, node_states)

        status_text = str(latest.get("status_text", ""))
        if not status_text:
            steps = latest.get("steps", [])
            status_text = steps[-1] if steps else "处理中..."
        status_text = f"{status_text} (⏱️ {elapsed}s)"

        if state.finished:
            if state.error:
                return (elements, f"❌ 任务失败: {state.error} (⏱️ {elapsed}s)",
                        True, False, True, True, dag, BAR_ERROR)
            return (elements,
                    "✅ 流程图渲染完成！请先在图中点击一个高亮（黄色）节点，再选择操作",
                    True, False, True, False, dag, BAR_DONE)

        return (elements, status_text, False, True, False, True, dag, BAR_LOADING)

    # ── 停止 ──
    @callback(
        Output("stream-interval", "disabled", allow_duplicate=True),
        Output("global-status", "children", allow_duplicate=True),
        Output("submit-btn", "disabled", allow_duplicate=True),
        Output("stop-btn", "disabled", allow_duplicate=True),
        Output("resume-btn", "disabled"),
        Input("stop-btn", "n_clicks"),
        State("thread-id-store", "data"),
        prevent_initial_call=True,
    )
    async def on_stop(n_clicks, thread_id):
        if not thread_id:
            return (True, "⚠️ 没有正在运行的任务", False, True, True)
        try:
            await cancel_stream(thread_id)
            return (True,
                    "⏹️ 任务已取消（后端 LLM 调用将在下次响应前终止）",
                    False, True, True)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(f"Failed to cancel stream {thread_id}")
            return (True, f"❌ 取消失败: {e}（任务可能仍在后台运行）",
                    False, True, True)

    # ── 开始执行根节点 ──
    @callback(
        Output("node-states-store", "data", allow_duplicate=True),
        Output("flowchart", "elements", allow_duplicate=True),
        Output("global-status", "children", allow_duplicate=True),
        Output("start-exec-btn", "disabled", allow_duplicate=True),
        Input("start-exec-btn", "n_clicks"),
        State("dag-store", "data"),
        State("node-states-store", "data"),
        prevent_initial_call=True,
    )
    def on_start_execution(n_clicks, dag, node_states):
        if not dag or not dag.get("nodes"):
            return no_update, no_update, "⚠️ 没有可执行的任务", True

        nodes = dag["nodes"]
        edges = dag.get("edges", [])
        valid_ids = {str(n["id"]) for n in nodes}
        in_deg: dict[str, int] = {}
        for e in edges:
            target = str(e.get("to", ""))
            if target in valid_ids:
                in_deg[target] = in_deg.get(target, 0) + 1

        new_states = dict(node_states or {})
        for node in nodes:
            nid = str(node["id"])
            if nid not in new_states and in_deg.get(nid, 0) == 0:
                new_states[nid] = "running"

        elements = cytoscape_cached(nodes, edges, new_states)
        running_count = sum(1 for v in new_states.values() if v == "running")
        if running_count > 0:
            status_msg = f"▶️ 正在执行 {running_count} 个根节点，完成后点击节点标记状态"
        else:
            status_msg = "⚠️ 未找到可执行的根节点（可能存在循环依赖），请检查流程图"

        return new_states, elements, status_msg, True

    # ═══════════════════════════════════════════════════════════
    #  节点操作：完成 / 跳过 / 失败 / 重置
    # ═══════════════════════════════════════════════════════════
    @callback(
        Output("node-states-store", "data", allow_duplicate=True),
        Output("flowchart", "elements", allow_duplicate=True),
        Output("global-status", "children", allow_duplicate=True),
        Output("progress-bar-fill", "style", allow_duplicate=True),
        Output("start-exec-btn", "disabled", allow_duplicate=True),
        Input("complete-btn", "n_clicks"),
        State("selected-node-store", "data"),
        State("dag-store", "data"),
        State("node-states-store", "data"),
        prevent_initial_call=True,
    )
    async def on_complete_node(n_clicks, selected_nid, dag, node_states):
        if not selected_nid or not dag:
            return (no_update,) * 5

        nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])
        new_states = dict(node_states or {})
        new_states[selected_nid] = "done"
        new_states = auto_unlock_downstream(dag, new_states, selected_nid, "done")
        elements = cytoscape_cached(nodes, edges, new_states)

        done_count = sum(1 for v in new_states.values() if v == "done")
        total = len(nodes)
        pct = int(done_count / total * 100) if total > 0 else 0
        status_msg = f"✅ 完成 {done_count}/{total} ({pct}%)"
        if pct >= 100:
            status_msg = "🎉 所有节点已完成！"

        bar_style = {
            "width": f"{pct}%", "height": "100%",
            "background": "linear-gradient(90deg, #10b981, #34d399)",
            "borderRadius": "6px", "transition": "width 0.3s ease",
        }
        return new_states, elements, status_msg, bar_style, False

    # ── 跳过（两个按钮触发） ──
    @callback(
        Output("node-states-store", "data", allow_duplicate=True),
        Output("flowchart", "elements", allow_duplicate=True),
        Output("global-status", "children", allow_duplicate=True),
        Output("progress-bar-fill", "style", allow_duplicate=True),
        Output("start-exec-btn", "disabled", allow_duplicate=True),
        Input("skip-btn", "n_clicks"),
        Input("skip-btn-panel", "n_clicks"),
        State("selected-node-store", "data"),
        State("dag-store", "data"),
        State("node-states-store", "data"),
        prevent_initial_call=True,
    )
    async def on_skip_node(n1, n2, selected_nid, dag, node_states):
        triggered_id = ctx.triggered_id
        if triggered_id not in ("skip-btn", "skip-btn-panel"):
            return (no_update,) * 5
        if not selected_nid or not dag:
            return no_update, no_update, "⚠️ 请先选择要跳过的节点", no_update, no_update

        nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])
        new_states = dict(node_states or {})
        new_states[selected_nid] = "skipped"
        new_states = auto_unlock_downstream(dag, new_states, selected_nid, "skipped")
        elements = cytoscape_cached(nodes, edges, new_states)

        done_count = sum(1 for v in new_states.values() if v == "done")
        skipped_count = sum(1 for v in new_states.values() if v == "skipped")
        total = len(nodes)
        processed = done_count + skipped_count
        pct = int(processed / total * 100) if total > 0 else 0

        status_msg = (
            f"⏭️ 已跳过 {selected_nid}（下游已按规则解锁）| "
            f"✅{done_count} ⏭️{skipped_count} / {total}"
        )
        bar_style = {
            "width": f"{pct}%", "height": "100%",
            "background": "linear-gradient(90deg, #6366f1, #818cf8)",
            "borderRadius": "6px", "transition": "width 0.3s ease",
        }
        return new_states, elements, status_msg, bar_style, False

    # ── 失败（两个按钮触发） ──
    @callback(
        Output("node-states-store", "data", allow_duplicate=True),
        Output("flowchart", "elements", allow_duplicate=True),
        Output("global-status", "children", allow_duplicate=True),
        Output("progress-bar-fill", "style", allow_duplicate=True),
        Output("start-exec-btn", "disabled", allow_duplicate=True),
        Input("fail-btn", "n_clicks"),
        Input("fail-btn-panel", "n_clicks"),
        State("selected-node-store", "data"),
        State("dag-store", "data"),
        State("node-states-store", "data"),
        prevent_initial_call=True,
    )
    async def on_fail_node(n1, n2, selected_nid, dag, node_states):
        triggered_id = ctx.triggered_id
        if triggered_id not in ("fail-btn", "fail-btn-panel"):
            return (no_update,) * 5
        if not selected_nid or not dag:
            return no_update, no_update, "⚠️ 请先选择要标记失败的节点", no_update, no_update

        nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])
        new_states = dict(node_states or {})
        new_states[selected_nid] = "failed"
        new_states = auto_unlock_downstream(
            dag, new_states, selected_nid, "failed",
            allowed_edge_types=("soft", "optional"),
        )
        elements = cytoscape_cached(nodes, edges, new_states)

        done_count = sum(1 for v in new_states.values() if v == "done")
        skipped_count = sum(1 for v in new_states.values() if v == "skipped")
        failed_count = sum(1 for v in new_states.values() if v == "failed")
        total = len(nodes)
        processed = done_count + skipped_count + failed_count
        pct = int(processed / total * 100) if total > 0 else 0

        status_msg = (
            f"❌ {selected_nid} 标记为「做不到」（强依赖下游已锁定）| "
            f"✅{done_count} ⏭️{skipped_count} ❌{failed_count}/{total}"
        )
        bar_style = {
            "width": f"{pct}%", "height": "100%",
            "background": "linear-gradient(90deg, #ef4444, #f87171)",
            "borderRadius": "6px", "transition": "width 0.3s ease",
        }
        return new_states, elements, status_msg, bar_style, False

    # ── 重置 ──
    @callback(
        Output("node-states-store", "data", allow_duplicate=True),
        Output("flowchart", "elements", allow_duplicate=True),
        Output("global-status", "children", allow_duplicate=True),
        Output("progress-bar-fill", "style", allow_duplicate=True),
        Output("start-exec-btn", "disabled", allow_duplicate=True),
        Input("reset-btn-panel", "n_clicks"),
        State("selected-node-store", "data"),
        State("dag-store", "data"),
        State("node-states-store", "data"),
        State("thread-id-store", "data"),
        prevent_initial_call=True,
    )
    async def on_reset_node(n_clicks, selected_nid, dag, node_states, task_id):
        from task_planner.core.database import reset_node_status
        import logging

        if ctx.triggered_id != "reset-btn-panel":
            return (no_update,) * 5
        if not selected_nid or not dag:
            return no_update, no_update, "⚠️ 请先选择要重置的节点", no_update, no_update

        try:
            nid_int = int(selected_nid)
        except (ValueError, TypeError):
            return no_update, no_update, f"⚠️ 无效节点ID: {selected_nid}", no_update, no_update

        nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])
        old_states = dict(node_states or {})

        db_ok = False
        has_task = bool(task_id)
        if has_task:
            try:
                db_ok = await reset_node_status(task_id, nid_int)
            except Exception as e:
                logging.getLogger(__name__).error(
                    "[Reset] DB 重置失败 task=%s node=%s: %s",
                    task_id, nid_int, e, exc_info=True,
                )

        new_states = dict(old_states)
        frontend_reset = False

        if has_task and not db_ok:
            pass
        else:
            if selected_nid in new_states:
                del new_states[selected_nid]
                frontend_reset = True

        elements = cytoscape_cached(nodes, edges, new_states)

        done_count = sum(1 for v in new_states.values() if v == "done")
        skipped_count = sum(1 for v in new_states.values() if v == "skipped")
        failed_count = sum(1 for v in new_states.values() if v == "failed")
        total = len(nodes)
        processed = done_count + skipped_count + failed_count
        pct = int(processed / total * 100) if total > 0 else 0

        bar_style = {
            "width": f"{pct}%", "height": "100%",
            "background": "linear-gradient(90deg, #6366f1, #818cf8)",
            "borderRadius": "6px", "transition": "width 0.3s ease",
        }

        if has_task and not db_ok:
            msg = f"❌ 节点 {selected_nid} 后端重置失败，前端状态未变更（请重试或检查日志）"
        elif frontend_reset:
            sync_label = "前端+后端同步" if has_task else "仅前端，无关联任务"
            msg = (
                f"🔄 节点 {selected_nid} 已重置（{sync_label}）| "
                f"✅{done_count} ⏭️{skipped_count} ❌{failed_count}/{total}"
            )
        else:
            msg = f"ℹ️ 节点 {selected_nid} 原本就是初始状态，无需重置"

        return new_states, elements, msg, bar_style, False
