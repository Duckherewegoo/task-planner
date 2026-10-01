"""commands.py — 任务命令（取消 / 修改 / 继续 / 重试）"""
from __future__ import annotations

import contextvars
from typing import Any

from langgraph.types import Command

from task_planner.core.graph.nodes import sanitize_input
from task_planner.core.graph.state import UserAction
from task_planner.core.graph.workflow import graph
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.utils.context import cancel_event_var

from .session import TaskSession, get_session

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  内部：resume 执行入口
# ═══════════════════════════════════════════════════════════════════
async def _resume_with_command(
    session: TaskSession,
    resume_data: dict[str, Any],
) -> None:
    """用 graph.ainvoke 恢复执行，确保 ContextVar 已绑定取消事件。"""
    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)
    await graph.ainvoke(Command(resume=resume_data), session.config)


# ═══════════════════════════════════════════════════════════════════
#  公开命令
# ═══════════════════════════════════════════════════════════════════
async def cancel_task(thread_id: str) -> bool:
    """取消任务（异步）"""
    session = await get_session(thread_id)
    if session:
        session.cancel_event.set()
        if session._runner_task and not session._runner_task.done():
            session._runner_task.cancel()
        logger.info("[Agent] 取消信号已发送 | thread=%s", thread_id)
        return True
    return False


async def modify_task(thread_id: str, new_input: str) -> bool:
    """修改任务需求（异步）"""
    session = await get_session(thread_id)
    if not session:
        return False

    try:
        cleaned = sanitize_input(new_input)
    except ValueError as e:
        logger.warning("[Agent] 修改输入校验失败: %s", e)
        return False

    await _resume_with_command(session, {
        "action": UserAction.MODIFY,
        "user_input": cleaned,
    })
    return True


async def resume_task(thread_id: str) -> bool:
    """继续执行（异步）"""
    session = await get_session(thread_id)
    if not session:
        return False

    await _resume_with_command(session, {"action": UserAction.CONTINUE})
    return True


async def retry_node_cmd(thread_id: str, node_id: int) -> bool:
    """重试指定节点（异步）"""
    session = await get_session(thread_id)
    if not session:
        return False

    await _resume_with_command(session, {
        "action": UserAction.RETRY_NODE,
        "node_id": node_id,
    })
    return True


__all__ = [
    "cancel_task",
    "modify_task",
    "resume_task",
    "retry_node_cmd",
]
