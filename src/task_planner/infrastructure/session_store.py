"""
session_store.py — 会话存储抽象与内存实现
═══════════════════════════════════════════════════════════════════════
为 Task Planner 的会话生命周期提供统一入口。
未来切换到 Redis 只需新增 RedisSessionStore，调用方零改动。

Changelog:
  ── v1 ──
  ✅ 提供 SessionStore Protocol + InMemorySessionStore 实现

  ── v2 ──
  ✅ P1-1：消除隐式依赖。原 set() 内部读 session.start_time，
           导致 store 耦合 session 的内部结构。
           改为 store 自记 created_at，真正与 session 解耦。
  ✅ P1-2：Protocol 加 @runtime_checkable，
           允许 isinstance(obj, SessionStore) 检查（浅检方法存在性）。
  ✅ P1-3：cleanup_stale 的 TTL 判断基于 store 自己的时间，
           不再受 session 内部时钟影响。
"""
from __future__ import annotations

import asyncio
import time
from typing import (
    Any,
    Optional,
    Protocol,
    runtime_checkable,
)


# ═══════════════════════════════════════════════════════════════════
#  接口
# ═══════════════════════════════════════════════════════════════════
@runtime_checkable
class SessionStore(Protocol):
    """
    会话存储接口。

    实现要求：
      - 所有方法必须协程安全
      - 存储内容完全不透明（Any），store 不假设 session 的内部结构
      - cleanup_stale 按 store 自己记录的创建时间判断过期

    用法：
        store: SessionStore = InMemorySessionStore()
        # 或未来：
        # store: SessionStore = RedisSessionStore(url=...)
    """

    async def get(self, tid: str) -> Optional[Any]: ...
    async def set(self, tid: str, session: Any, *, created_at: float | None = None,) -> None: ...
    async def pop(self, tid: str) -> Optional[Any]: ...
    async def cleanup_stale(self, ttl: int) -> int: ...


# ═══════════════════════════════════════════════════════════════════
#  内存实现
# ═══════════════════════════════════════════════════════════════════
class InMemorySessionStore:
    """
    单进程内存实现。

    ⚠️ 仅适用于单 worker 部署。
       多 worker（gunicorn/uvicorn --workers > 1）场景下，
       请求可能落到不同进程，会话状态不共享。
       生产环境应使用 RedisSessionStore。

    存储结构：
        _data[tid] = {
            "session":    <任意对象>,   # 调用方自己管
            "created_at": <float>,      # store 自己记，用于 TTL
        }
    """

    __slots__ = ("_data", "_lock")

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # ── 读 ──
    async def get(self, tid: str) -> Optional[Any]:
        async with self._lock:
            entry = self._data.get(tid)
            if not entry:
                return None
            return entry["session"]

    # ── 写 ──
    async def set(
        self,
        tid: str,
        session: Any,
        *,
        created_at: float | None = None,
    ) -> None:
        """
        写入会话。

        Args:
            tid:         会话 ID
            session:     任意对象（store 不关心内部结构）
            created_at:  可选。指定创建时间（测试模拟旧会话用）。
                         不传时：
                           - 若已存在，保留原 created_at（不刷新 TTL）
                           - 若不存在，用当前时间
        """
        async with self._lock:
            existing = self._data.get(tid)
            if created_at is not None:
                ts = created_at
            elif existing is not None:
                ts = existing["created_at"]
            else:
                ts = time.time()
            self._data[tid] = {
                "session": session,
                "created_at": ts,
            }

    # ── 移除 ──
    async def pop(self, tid: str) -> Optional[Any]:
        async with self._lock:
            entry = self._data.pop(tid, None)
            return entry["session"] if entry else None

    # ── 清理 ──
    async def cleanup_stale(self, ttl: int) -> int:
        """
        清理超过 ttl 秒未更新的会话。
        ✅ P1-3：基于 store 自记的 created_at，不依赖 session 内部时钟。
        """
        now = time.time()
        async with self._lock:
            stale = [
                k for k, v in self._data.items()
                if now - v["created_at"] > ttl
            ]
            for k in stale:
                self._data.pop(k, None)
        return len(stale)

    # ── 便于测试 / 健康检查 ──
    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, tid: str) -> bool:
        return tid in self._data


__all__ = ["SessionStore", "InMemorySessionStore"]
