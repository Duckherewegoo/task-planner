"""
session.py — TaskSession 定义 + 会话池管理
═══════════════════════════════════════════════════════════════════════
- TaskSession: 单个任务的运行时状态（事件 / 队列 / config / runner_task）
- 会话池: 全局单例 SessionStore + asyncio.Lock
- 提供: get_session / acquire_session / cleanup_stale_sessions
"""
from __future__ import annotations

import asyncio
import time
from typing import (
    Any,
    Optional,
)

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.session_store import (
    InMemorySessionStore,
    SessionStore,
)

logger = get_logger(__name__)

_SESSION_TTL = _hub.dev.SESSION_TTL


# ═══════════════════════════════════════════════════════════════════
#  TaskSession
# ═══════════════════════════════════════════════════════════════════
class TaskSession:
    """单个任务会话 - 完全异步化"""

    __slots__ = (
        "thread_id",
        "user_input",
        "cancel_event",
        "start_time",
        "progress_q",
        "_runner_task",
        "config",
    )

    def __init__(self, thread_id: str, user_input: str):
        self.thread_id = thread_id
        self.user_input = user_input

        self.cancel_event = asyncio.Event()
        self.start_time = time.time()

        # 有界队列，最多缓存 100 条进度；满了丢最老
        self.progress_q: asyncio.Queue = asyncio.Queue(maxsize=100)

        self._runner_task: Optional[asyncio.Task] = None

        # LangGraph 兼容
        self.config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id}
        }

    async def emit(self, msg: str) -> None:
        try:
            self.progress_q.put_nowait(msg)
        except asyncio.QueueFull:
            try:
                self.progress_q.get_nowait()
                self.progress_q.put_nowait(msg)
            except asyncio.QueueEmpty:
                pass
        logger.info("[Agent] %s", msg)


# ═══════════════════════════════════════════════════════════════════
#  会话池（全局单例）
# ═══════════════════════════════════════════════════════════════════
_session_store: SessionStore = InMemorySessionStore()
_sessions_lock = asyncio.Lock()


async def get_session(thread_id: str) -> Optional[TaskSession]:
    """异步获取会话"""
    return await _session_store.get(thread_id)


async def pop_session(thread_id: str) -> Optional[TaskSession]:
    """异步移除会话（供 stream 的 finally 使用）"""
    return await _session_store.pop(thread_id)


async def set_session(thread_id: str, session: TaskSession) -> None:
    """写入会话"""
    await _session_store.set(thread_id, session)


async def cleanup_stale_sessions() -> int:
    """定期清理过期会话（可由后台任务调用）"""
    return await _session_store.cleanup_stale(_SESSION_TTL)


async def acquire_session(
    thread_id: str,
    user_input: str,
    resume: bool,
) -> TaskSession:
    """
    获取或创建 session。
    resume 时复用旧 session 的 cancel_event（若已 set 则重置）。
    """
    async with _sessions_lock:
        if resume:
            existing = await _session_store.get(thread_id)
            if existing is not None:
                if existing.cancel_event.is_set():
                    existing.cancel_event = asyncio.Event()
                logger.info("[Agent] resume 复用已有 session | thread=%s", thread_id)
                return existing

        session = TaskSession(thread_id, user_input)
        await _session_store.set(thread_id, session)
        return session


__all__ = [
    "TaskSession",
    "get_session",
    "pop_session",
    "set_session",
    "acquire_session",
    "cleanup_stale_sessions",
]
