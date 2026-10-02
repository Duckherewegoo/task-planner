"""direct.py — 直接回答节点（不规划）"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.db import create_direct_answer_task
from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import direct_chat
from task_planner.infrastructure.logger_setup import (
    get_logger,
    set_req_id,
)

from .sanitize import get_cancel_event

logger = get_logger(__name__)


async def direct_answer_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """直接回答（异步）"""
    rid = set_req_id()
    logger.info("[Graph] direct_answer_node (req=%s)", rid)

    cancel_event = get_cancel_event(config)
    answer = await direct_chat(state["user_input"], rid, cancel_event=cancel_event)

    try:
        await create_direct_answer_task(
            raw_query=state["user_input"],
            intent_info=state.get("intent", {}),
            response=answer,
            req_id=rid,
        )
    except Exception as e:
        logger.warning("[Graph] 直接回答入库失败(不影响返回): %s", e)

    return {
        "direct_response": answer,
        "messages": [
            {"role": "user", "content": state["user_input"]},
            {"role": "assistant", "content": answer},
        ],
        "steps": state.get("steps", []) + ["直接回答完成"],
    }
