"""intent.py — 意图识别节点"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import recognize_intent
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

from .sanitize import get_cancel_event, sanitize_input

logger = get_logger(__name__)


async def intent_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """意图识别节点（异步）"""
    rid = set_req_id()
    logger.info("[Graph] intent_node (req=%s)", rid)

    user_input = sanitize_input(state["user_input"])
    cancel_event = get_cancel_event(config)
    intent = await recognize_intent(user_input, req_id=rid, cancel_event=cancel_event)

    return {
        "user_input": user_input,
        "intent": intent,
        "needs_planning": intent.get("needs_planning", False),
        # 无 reducer 方案：手动累积 steps
        "steps": state.get("steps", []) + [
            f"意图识别: planning={intent.get('needs_planning')}"
        ],
    }
