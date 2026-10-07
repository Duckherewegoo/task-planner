"""stream.py — run_task_stream 核心生成器"""
from __future__ import annotations

import asyncio
import contextvars
import traceback
from collections.abc import AsyncGenerator
from typing import (
    Any,
)

from task_planner.core.graph.workflow import graph
from task_planner.infrastructure.logger_setup import (
    get_logger,
    set_req_id,
)
from task_planner.utils.context import cancel_event_var

from .session import (
    acquire_session,
    pop_session,
)
from .state import make_initial_state
from .view import (
    extract_snapshot,
    is_graph_finished,
)

logger = get_logger(__name__)


async def run_task_stream(
    user_input: str,
    thread_id: str,
    enable_refine: bool = True,
    resume: bool = False,
) -> AsyncGenerator[dict[str, Any]]:
    """
    异步生成器，实时推送任务执行进度。
    调用方应使用 `async for event in run_task_stream(...):` 消费。
    """
    if not isinstance(thread_id, str):
        raise TypeError(
            f"thread_id must be str, got {type(thread_id).__name__}: {thread_id!r}"
        )

    rid = set_req_id()
    logger.info("[Agent] 启动任务 | thread=%s req=%s", thread_id, rid)

    session = await acquire_session(thread_id, user_input, resume)
    session._runner_task = asyncio.current_task()

    # 用 copy_context 绑定，避免跨 Task 恢复时 token 语义错位
    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)

    try:
        event_count = 0

        if resume:
            logger.info("[Agent] resume 模式 | thread=%s", thread_id)
            stream_iter = graph.astream(
                None,
                session.config,
                stream_mode=["values", "updates"],
            )
        else:
            initial_state = make_initial_state(user_input, thread_id)
            logger.info(
                "[Agent] initial_state keys: %s", list(initial_state.keys())
            )
            stream_iter = graph.astream(
                initial_state,
                session.config,
                stream_mode=["values", "updates"],
            )

        async for event in stream_iter:
            event_count += 1

            if isinstance(event, tuple):
                mode, data = event
                logger.info(
                    "[Agent] event #%d [%s] received", event_count, mode
                )
            else:
                mode, data = "values", event
                logger.info("[Agent] event #%d received", event_count)

            if session.cancel_event.is_set():
                logger.info("[Agent] 收到取消信号 (req=%s)", rid)
                break

            snapshot = extract_snapshot(data, session, mode=mode)
            if snapshot is not None:
                yield snapshot

            if is_graph_finished(data, mode):
                break

        logger.info("[Agent] graph.astream() 结束，共 %d 个事件", event_count)

    except asyncio.CancelledError:
        session.cancel_event.set()
        logger.warning("[Agent] 任务被外部取消 (req=%s)", rid)
        yield {"type": "cancelled", "task_id": thread_id}
        raise

    except Exception as exc:
        logger.error("[Agent] 异常: %s\n%s", exc, traceback.format_exc())
        yield {
            "type": "error",
            "error": str(exc),
            "task_id": thread_id,
        }

    finally:
        session.cancel_event.set()
        await pop_session(thread_id)
        logger.info("[Agent] session 清理完成 | thread=%s req=%s", thread_id, rid)


__all__ = ["run_task_stream"]
