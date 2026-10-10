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

  __ V3 __
  将源文件拆包，只保留独立的protocol接口，便于修改和观察
"""
from __future__ import annotations

import asyncio
import time
from typing import (
    Any,
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

    async def get(self, tid: str) -> Any | None: ...
    async def set(self, tid: str, session: Any, *, created_at: float | None = None,) -> None: ...
    async def pop(self, tid: str) -> Any | None: ...
    async def cleanup_stale(self, ttl: int) -> int: ...



__all__ = ["SessionStore"]
