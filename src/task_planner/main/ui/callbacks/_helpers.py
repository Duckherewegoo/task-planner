import asyncio
from collections.abc import Coroutine
from typing import Any

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.ui.callbacks")

_background_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """fire-and-forget 一个协程，保留引用防 GC，异常记日志不静默丢失。

    必须在 uvicorn 的 event loop 内调用（即 async 回调里）。
    返回 Task，调用方需要时可 set_task / await，不需要就忽略。
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("[Background] 后台任务异常: %s", exc, exc_info=exc)

    task.add_done_callback(_on_done)
    return task


__all__ = ["spawn"]
