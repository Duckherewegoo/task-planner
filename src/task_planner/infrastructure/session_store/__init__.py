"""
session_store — 会话存储抽象与实现
═══════════════════════════════════════════════════════════════════════
包结构：
    protocol.py（或 session_store.py）  ← SessionStore Protocol
    memory.py（或 session_store.py）    ← InMemorySessionStore
    redis_session_store.py              ← RedisSessionStore

对外统一入口：
    from task_planner.infrastructure.session_store import (
        SessionStore, InMemorySessionStore, RedisSessionStore,
    )

⚠️ RedisSessionStore 依赖 redis 包——未安装时 import 该名称会抛 ImportError。
   内存版和 Protocol 始终可用，不受 redis 依赖影响。

   安全用法：
       from ...session_store import SessionStore, InMemorySessionStore  # ✅ 永远可用
       from ...session_store import RedisSessionStore                   # ⚠️ 需要 redis
"""
from __future__ import annotations

from .in_memory_session_store import InMemorySessionStore
# ── 核心：Protocol + 内存实现（无外部依赖，永远可用）──
from .protocol import SessionStore

# ── Redis 实现（可选依赖，缺席时降级）──
try:
    from .redis_session_store import RedisSessionStore
    _HAS_REDIS_IMPL = True
except ImportError:
    # redis 客户端没装——但内存版仍可用
    RedisSessionStore = None  # type: ignore
    _HAS_REDIS_IMPL = False


__all__ = [
    "SessionStore",
    "InMemorySessionStore",
    "RedisSessionStore",
    "_HAS_REDIS_IMPL",
]
