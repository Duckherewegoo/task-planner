"""execute.py — 单节点执行节点（含 interrupt + MODIFY 重置）"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from task_planner.core.database import mark_task_running, update_node_status
from task_planner.core.graph.state import TaskState, UserAction
from task_planner.infrastructure.llm_client import LLMCancelledError
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

from ._executor import execute_single_node
from .sanitize import get_cancel_event, sanitize_input, sanitize_node_for_user

logger = get_logger(__name__)


async def execute_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """执行节点（异步 + interrupt + 异常分级）"""
    rid = set_req_id()
    logger.info("[Graph] execute_node (req=%s)", rid)

    nodes = state["nodes"]
    idx = state.get("current_node_index", 0)

    if idx >= len(nodes):
        return {"user_action": None}

    node = nodes[idx]
    nid = node.get("id", idx)
    name = node.get("name", f"步骤{nid}")

    await mark_task_running(state["task_id"])

    cancel_event = get_cancel_event(config)

    # ── 异常分级响应 ──
    try:
        result = await execute_single_node(
            node=node,
            user_input=state["user_input"],
            intent=state["intent"],
            cancel_event=cancel_event,
            req_id=rid,
        )
    except LLMCancelledError:
        logger.info("[Graph] execute_node: 取消信号，跳过 interrupt (req=%s)", rid)
        return {
            "cancel_requested": True,
            "status_text": "⏹️ 已取消",
            "steps": state.get("steps", []) + [f"节点{nid} ⏹️ 用户取消"],
        }

    success = result["status"] == "success"
    detail = result["detail"]

    status_code = 2 if success else 3
    await update_node_status(state["task_id"], nid, status_code, details=detail)

    node_results = state.get("node_results", []) + [
        {"node_id": nid, "success": success, "detail": detail}
    ]

    icon = "✅" if success else "❌"
    steps = state.get("steps", []) + [f"节点{nid} {icon} {name}"]

    user_visible_node = sanitize_node_for_user(node)
    interrupt_payload = {
        "type": "node_complete",
        "node_id": nid,
        "node_name": name,
        "success": success,
        "detail": detail,
        "node_info": user_visible_node,
        "next_index": idx + 1,
        "total_nodes": len(nodes),
        "retryable": result.get("retryable", False),
    }

    feedback = interrupt(interrupt_payload)

    action = feedback.get("action", UserAction.CONTINUE)
    modified_input = feedback.get("user_input", None)

    updates: dict[str, Any] = {
        "current_node_index": idx + 1,
        "node_results": node_results,
        "steps": steps,
        "user_action": action,
    }

    if action == UserAction.MODIFY and modified_input:
        # 重置所有"上一轮残留"状态，避免 steps 污染前端
        cleaned = sanitize_input(modified_input)
        updates.update({
            "user_input": cleaned,
            "current_node_index": 0,
            "node_results": [],
            "nodes": [],
            "edges": [],
            "refined_nodes": [],
            "plan": {},
            "intent": {},
            "task_id": "",
            "steps": [f"需求已修改，重新规划: {cleaned[:50]}"],
            "direct_response": "",
            "error": "",
            "status_text": "🔄 重新规划中",
            "cancel_requested": False,
            "user_action": None,
            "svg": "",
            "flowchart_html": "",
        })

    return updates
