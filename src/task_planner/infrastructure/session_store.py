"""
session_store.py — 会话存储抽象与内存实现

为 Task Planner 的会话生命周期提供统一入口。
未来切换到 Redis 只需新增 RedisSessionStore，调用方零改动。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Protocol


class SessionStore(Protocol):
    """会话存储接口"""

    async def get(self, tid: str) -> Optional[Any]: ...
    async def set(self, tid: str, session: Any) -> None: ...
    async def pop(self, tid: str) -> Optional[Any]: ...
    async def cleanup_stale(self, ttl: int) -> int: ...


class InMemorySessionStore:
    """
    单进程内存实现。

    ⚠️ 仅适用于单 worker 部署。
       多 worker（gunicorn/uvicorn --workers > 1）场景下，
       请求可能落到不同进程，会话状态不共享。
       生产环境应使用 RedisSessionStore。
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, tid: str) -> Optional[Any]:
        async with self._lock:
            entry = self._data.get(tid)
            if not entry:
                return None
            return entry["session"]

    async def set(self, tid: str, session: Any) -> None:
        async with self._lock:
            self._data[tid] = {
                "session": session,
                "start_time": session.start_time,
            }

    async def pop(self, tid: str) -> Optional[Any]:
        async with self._lock:
            entry = self._data.pop(tid, None)
            return entry["session"] if entry else None

    async def cleanup_stale(self, ttl: int) -> int:
        now = time.time()
        async with self._lock:
            stale = [
                k for k, v in self._data.items()
                if now - v["start_time"] > ttl
            ]
            for k in stale:
                self._data.pop(k, None)
        return len(stale)
