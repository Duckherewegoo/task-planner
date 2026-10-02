"""cancel.py — 取消节点"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.db import mark_task_failed
from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


async def cancel_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """取消节点（异步）"""
    logger.info("[Graph] cancel_node")
    if state.get("task_id"):
        try:
            await mark_task_failed(state["task_id"], error="用户取消")
        except (RuntimeError, TimeoutError) as e:
            logger.warning("[Graph] cancel_node: mark_task_failed failed: %s", e)

    return {
        "cancel_requested": True,
        "status_text": "⏹️ 已取消",
        "steps": state.get("steps", []) + ["任务已取消"],
    }
