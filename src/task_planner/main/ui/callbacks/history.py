"""history.py — 历史记录回调"""
from __future__ import annotations

import logging

from dash import Input, Output, State, callback, dcc, no_update

from task_planner.core.database import (
    batch_delete_tasks,
    list_tasks,
    load_task_with_plan,
)
from task_planner.core.graph.nodes import sanitize_node_for_user
from task_planner.infrastructure.constants import (
    NODE_OPERABLE_STATES,
    NODE_STATUS_CODE_MAP,
)
from task_planner.utils.cytoscape_adapter import (
    build_detail_markdown,
    build_history_detail_markdown,
)
from task_planner.utils.presentation_utils import build_history_dropdown_options

from ..constants import HistorySelectResult, HistoryTapNodeResult, cytoscape_cached
from ..data_ops import load_and_fill_query, update_node_and_render

logger = logging.getLogger(__name__)


def register() -> None:
    @callback(
        Output("history-dropdown", "options"),
        Output("history-dropdown", "placeholder"),
        Input("refresh-btn", "n_clicks"),
        Input("main-tabs", "value"),
        prevent_initial_call=True,
    )
    async def load_history_list(n_clicks, tab_value):
        if tab_value != "tab-history":
            return no_update, no_update
        try:
            tasks = await list_tasks(50)
        except Exception as e:
            logger.error("[History] 加载任务列表失败: %s", e, exc_info=True)
            return [], "❌ 加载失败，请检查后端服务"
        if not tasks:
            return [], "暂无历史记录"
        sorted_tasks = sorted(
            tasks, key=lambda t: t.get("created", ""), reverse=True
        )
        options = []
        for t in sorted_tasks:
            raw_title = t.get("title") or ""
            safe_title = str(raw_title)[:30] if raw_title else "(无标题)"
            tid_suffix = str(t.get("task_id", ""))[-8:]
            options.append({
                "label": f"{safe_title} | {tid_suffix}",
                "value": t["task_id"],
            })
        return options, "请选择历史任务"

    @callback(
        Output("history-flowchart", "elements"),
        Output("history-detail", "children"),
        Output("history-flowchart", "pan"),
        Output("global-status", "children", allow_duplicate=True),
        Output("dag-store", "data", allow_duplicate=True),
        Output("history-replan-btn", "disabled"),
        Output("history-edit-plan-btn", "disabled"),
        Output("history-resume-btn", "disabled"),
        Output("history-retry-btn", "disabled"),
        Output("history-action-status", "children"),
        Output("history-dag-store", "data"),
        Output("history-node-states-store", "data"),
        Input("history-dropdown", "value"),
        prevent_initial_call=True,
    )
    async def on_history_select(task_ids):
        if not task_ids:
            return HistorySelectResult().to_tuple()

        task_id = task_ids[0] if isinstance(task_ids, list) else task_ids

        try:
            data = await load_task_with_plan(task_id)
        except Exception as e:
            logger.error(
                "[History] 加载任务失败 task=%s: %s", task_id, e, exc_info=True
            )
            return HistorySelectResult(
                global_status="❌ 加载失败，请检查后端服务",
                action_status="❌ 加载失败",
            ).to_tuple()

        if not data:
            return HistorySelectResult(
                global_status="⚠️ 任务不存在",
                action_status="⚠️ 任务不存在",
            ).to_tuple()

        task_info = data["task"]
        plan_data = data.get("plan")

        if not plan_data:
            direct_resp = task_info.get("direct_response") or "（无回答内容）"
            raw_query = task_info.get("raw_query") or ""
            md_text = (
                f"## 💬 直接回答\n\n"
                f"**用户提问：**\n\n{raw_query}\n\n---\n\n"
                f"**AI 回答：**\n\n{direct_resp}\n\n---\n\n"
                f"*任务 ID: {task_id} | 类型: 直接回答（无规划流程）*"
            )
            return HistorySelectResult(
                detail=dcc.Markdown(md_text, style={"fontSize": "14px", "lineHeight": "1.8"}),
                global_status="💬 直接回答任务已加载",
                action_status="💡 这是一个直接回答类会话，无需规划流程。可在新建任务中重新提问。",
            ).to_tuple()

        nodes = plan_data.get("nodes", [])
        edges = plan_data.get("edges", [])

        node_states = {
            str(n.get("id", "")): NODE_STATUS_CODE_MAP.get(n.get("status", 0), "pending")
            for n in nodes
        }

        elements = cytoscape_cached(nodes, edges, node_states)
        md_text = build_history_detail_markdown(task_id, nodes)
        dag_store_data = {"nodes": nodes, "edges": edges, "task_id": task_id}

        has_failed = any(v == "failed" for v in node_states.values())
        has_pending = any(v in ("pending", "blocked") for v in node_states.values())
        all_done = all(v in ("done", "skipped") for v in node_states.values())

        pending_count = sum(1 for v in node_states.values() if v in ("pending", "blocked"))
        hint_parts = []
        if all_done:
            hint_parts.append("✅ 所有节点已完成")
        if has_failed:
            hint_parts.append("❌ 存在失败节点，可重试")
        if has_pending:
            hint_parts.append(f"⏳ 还有 {pending_count} 个节点待执行")
        action_hint = " | ".join(hint_parts) if hint_parts else "✅ 任务状态正常"

        return HistorySelectResult(
            elements=elements,
            detail=dcc.Markdown(md_text, style={"fontSize": "14px", "lineHeight": "1.8"}),
            global_status=f"✅ 已加载历史流程图（{len(nodes)} 个节点）",
            dag_store=dag_store_data,
            replan_disabled=False,
            edit_disabled=False,
            resume_disabled=not has_pending,
            retry_disabled=not has_failed,
            action_status=action_hint,
            history_dag_store=dag_store_data,
            history_node_states=node_states,
        ).to_tuple()

    @callback(
        Output("history-node-detail", "children"),
        Output("history-selected-node-store", "data"),
        Output("history-node-done-btn", "disabled"),
        Output("history-node-skip-btn", "disabled"),
        Output("history-node-fail-btn", "disabled"),
        Input("history-flowchart", "tapNodeData"),
        State("history-node-states-store", "data"),
        prevent_initial_call=True,
    )
    def on_history_tap_node(node_data, node_states):
        if not node_data:
            return HistoryTapNodeResult().to_tuple()

        nid = str(node_data.get("id", ""))
        if not nid:
            logger.warning("[History] tapNodeData 缺少 id 字段: %s", node_data)
            return HistoryTapNodeResult().to_tuple()

        node_states = node_states or {}
        current_state = str(node_states.get(nid, "pending"))
        can_operate = current_state in NODE_OPERABLE_STATES

        try:
            clean_data = sanitize_node_for_user(dict(node_data))
            md_text = build_detail_markdown(clean_data)
        except Exception as e:
            logger.error(
                "[History] 构建节点详情失败 node=%s: %s", nid, e, exc_info=True
            )
            md_text = f"**⚠️ 节点详情加载失败**\n\n`{nid}`"

        return HistoryTapNodeResult(
            detail=dcc.Markdown(md_text, style={"fontSize": "13px", "lineHeight": "1.8"}),
            selected_node=nid,
            done_disabled=not can_operate,
            skip_disabled=not can_operate,
            fail_disabled=not can_operate,
        ).to_tuple()

    @callback(
        Output("history-dropdown", "options", allow_duplicate=True),
        Output("history-dropdown", "value", allow_duplicate=True),
        Output("delete-status", "children"),
        Input("delete-btn", "n_clicks"),
        State("history-dropdown", "value"),
        prevent_initial_call=True,
    )
    async def on_delete(n_clicks, task_ids):
        if not task_ids:
            return no_update, no_update, "⚠️ 请先选择要删除的任务"

        ids = task_ids if isinstance(task_ids, list) else [task_ids]

        try:
            count = await batch_delete_tasks(ids)
            logger.info("[History] 删除成功 tasks=%s count=%d", ids, count)
        except Exception as e:
            logger.error(
                "[History] 删除失败 tasks=%s: %s", ids, e, exc_info=True
            )
            return no_update, no_update, "❌ 删除失败，请稍后重试或联系管理员"

        try:
            tasks = await list_tasks(50)
            options = build_history_dropdown_options(tasks)
        except Exception as e:
            logger.error("[History] 删除后刷新列表失败: %s", e, exc_info=True)
            return no_update, None, f"✅ 已删除 {count} 个任务，但列表刷新失败，请手动刷新页面"

        return options, None, f"✅ 已删除 {count} 个任务"

    @callback(
        Output("global-status", "children", allow_duplicate=True),
        Output("main-tabs", "value"),
        Output("user-input", "value"),
        Input("history-replan-btn", "n_clicks"),
        State("history-dropdown", "value"),
        prevent_initial_call=True,
    )
    async def on_history_replan(n_clicks, task_ids):
        msg, tab, query = await load_and_fill_query(task_ids, action_label="重新规划")
        return msg, tab, query

    @callback(
        Output("global-status", "children", allow_duplicate=True),
        Output("main-tabs", "value", allow_duplicate=True),
        Output("user-input", "value", allow_duplicate=True),
        Input("history-resume-btn", "n_clicks"),
        State("history-dropdown", "value"),
        prevent_initial_call=True,
    )
    async def on_history_resume(n_clicks, task_ids):
        msg, tab, query = await load_and_fill_query(task_ids, action_label="继续执行")
        return msg, tab, query

    # ── 历史节点操作：完成 / 跳过 / 失败 ──
    @callback(
        Output("history-node-states-store", "data", allow_duplicate=True),
        Output("history-flowchart", "elements", allow_duplicate=True),
        Output("history-action-status", "children", allow_duplicate=True),
        Input("history-node-done-btn", "n_clicks"),
        State("history-selected-node-store", "data"),
        State("history-dag-store", "data"),
        State("history-node-states-store", "data"),
        prevent_initial_call=True,
    )
    def on_history_node_done(n_clicks, selected_nid, dag, node_states):
        if not selected_nid or not node_states or not dag:
            return no_update, no_update, no_update
        try:
            new_states, elements, status_msg = update_node_and_render(
                dag, node_states, selected_nid, target_status="done"
            )
        except Exception as e:
            logger.error(
                "[NodeAction] 标记完成失败 node=%s: %s", selected_nid, e, exc_info=True
            )
            return no_update, no_update, f"❌ 状态更新失败: {selected_nid}"
        logger.info("[NodeAction] 手动标记完成 node=%s", selected_nid)
        return new_states, elements, status_msg

    @callback(
        Output("history-node-states-store", "data", allow_duplicate=True),
        Output("history-flowchart", "elements", allow_duplicate=True),
        Output("history-action-status", "children", allow_duplicate=True),
        Input("history-node-skip-btn", "n_clicks"),
        State("history-selected-node-store", "data"),
        State("history-dag-store", "data"),
        State("history-node-states-store", "data"),
        prevent_initial_call=True,
    )
    def on_history_node_skip(n_clicks, selected_nid, dag, node_states):
        if not selected_nid or not node_states or not dag:
            return no_update, no_update, no_update
        try:
            new_states, elements, status_msg = update_node_and_render(
                dag, node_states, selected_nid, target_status="skipped"
            )
        except Exception as e:
            logger.error(
                "[NodeAction] 跳过失败 node=%s: %s", selected_nid, e, exc_info=True
            )
            return no_update, no_update, f"❌ 跳过操作失败: {selected_nid}"
        logger.info("[NodeAction] 手动跳过 node=%s", selected_nid)
        return new_states, elements, status_msg

    @callback(
        Output("history-node-states-store", "data", allow_duplicate=True),
        Output("history-flowchart", "elements", allow_duplicate=True),
        Output("history-action-status", "children", allow_duplicate=True),
        Input("history-node-fail-btn", "n_clicks"),
        State("history-selected-node-store", "data"),
        State("history-dag-store", "data"),
        State("history-node-states-store", "data"),
        prevent_initial_call=True,
    )
    def on_history_node_fail(n_clicks, selected_nid, dag, node_states):
        if not selected_nid or not node_states or not dag:
            return no_update, no_update, no_update
        try:
            new_states, elements, status_msg = update_node_and_render(
                dag, node_states, selected_nid, target_status="failed"
            )
        except Exception as e:
            logger.error(
                "[NodeAction] 标记失败异常 node=%s: %s", selected_nid, e, exc_info=True
            )
            return no_update, no_update, f"❌ 标记失败操作异常: {selected_nid}"
        logger.warning("[NodeAction] 手动标记失败 node=%s", selected_nid)
        return new_states, elements, status_msg
