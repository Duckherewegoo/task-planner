"""
session.py — TaskSession 定义 + 会话池管理
═══════════════════════════════════════════════════════════════════════
- TaskSession: 单个任务的运行时状态（事件 / 队列 / config / runner_task）
- 会话池: 全局单例 SessionStore + asyncio.Lock
- 提供: get_session / acquire_session / cleanup_stale_sessions

⚠️ 关于 _sessions_lock 的 loop 绑定：
   模块级 asyncio.Lock 会在首次 await 时绑定当前事件循环。
   生产是单 loop 常驻没问题；但 pytest-asyncio（每用例新 loop）
   或任何"进程内多 loop"场景，第二次使用会抛
   "is bound to a different event loop"。
   本文件用 _get_sessions_lock() 惰性创建 + loop 变化检测——
   loop 变了就重建锁（语义上等价于"新进程"）。

⚠️ 关于 cancel_event 的重置：
   acquire_session 复用旧 session 时若 cancel_event 已 set，
   用 clear() 重置而不是替换对象——保留引用，
   让所有持有者（runner task / contextvar / 未来扩展）看到同一对象。

✅ SessionStore 工厂化（已完成）：
   通过 SESSION_STORE_TYPE 环境变量或 _hub.dev.SESSION_STORE_TYPE 控制：
     - "memory"（默认）→ InMemorySessionStore
     - "redis" → RedisSessionStore（需 pip install -e ".[redis]"）
   Redis 连接地址从 REDIS_URL 环境变量读取，默认 redis://localhost:6379/0。

✅ 后台清理任务（已完成）：
   app.py 的 FastAPI startup 事件中已 create_task 循环调用
   cleanup_stale_sessions()，不再有内存泄漏风险。

DESIGN NOTE(hot-reload): 本模块不缓存 _hub.dev 的配置值，每次实时读取。
# 这确保 hub.reload() 后新配置立即生效。如果未来为了性能引入本地缓存
# （例如高频路径上的 config 读取），必须监听 hub.reload 信号并失效缓存，
# 否则热重载后代码仍在使用旧值。
"""

import asyncio
import os
import time
from typing import Any

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.session_store import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionStore,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# TaskSession
# ═══════════════════════════════════════════════════════════════════
class TaskSession:
    """
    单个任务会话——**agent 侧状态**。

    与 TaskStreamState 的边界：
      - TaskSession：agent / graph 执行所需——cancel_event / start_time / config
      - TaskStreamState：UI 展示所需——snapshots / node_states / finished

    两个对象由两个 registry 分别管理（session_store / state_cleaner）——
    这是职责分离，不是技术债。详见 docs/DESIGN_task_context.md。
    """

    __slots__ = (
        "__weakref__",
        "cancel_event",
        "start_time",
        "thread_id",
    )

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.cancel_event = asyncio.Event()
        self.start_time = time.time()

    @property
    def config(self) -> dict[str, Any]:
        """
        LangGraph 配置——从 thread_id 现场构造。

        历史：曾是 __init__ 里存的 dict 字段——改 @property 消除
        "config 与 thread_id 可能漂移"的隐患。
        """
        return {"configurable": {"thread_id": self.thread_id}}


# ═══════════════════════════════════════════════════════════════════
# SessionStore 工厂
# ═══════════════════════════════════════════════════════════════════
def _create_session_store() -> SessionStore:
    """
    根据配置创建 SessionStore 实例。

    优先级：环境变量 SESSION_STORE_TYPE > _hub.dev.SESSION_STORE_TYPE > "memory"
    Redis URL：环境变量 REDIS_URL > "redis://localhost:6379/0"
    """
    store_type = os.getenv(
        "SESSION_STORE_TYPE", getattr(_hub.dev, "SESSION_STORE_TYPE", "memory")
    ).lower()
    ttl = getattr(_hub.dev, "_SESSION_TTL", 3600)

    if store_type == "redis":
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        logger.info(
            "[Session] 使用 Redis 会话存储 | Using Redis session store: %s",
            redis_url.split("://")[0],  # 不打印密码
        )
        return RedisSessionStore(redis_url, default_ttl=ttl)
    else:
        logger.info("[Session] 使用内存会话存储 | Using in-memory session store")
        return InMemorySessionStore()


# ═══════════════════════════════════════════════════════════════════
# 会话池（全局单例）
# ═══════════════════════════════════════════════════════════════════
_session_store: SessionStore | None = None


def _get_session_store() -> SessionStore:
    """获取 SessionStore 单例（惰性创建）"""
    global _session_store
    if _session_store is None:
        _session_store = _create_session_store()
    return _session_store


# 惰性创建 + loop 绑定检测：
# asyncio.Lock 在 Python 3.10+ 首次 await 时绑定当前 loop，
# 跨 loop 复用会抛 RuntimeError。这里检测 loop 变化后重建，
# 让测试/CLI 场景（进程内多 loop）不崩。
# 生产单 loop 场景下，_sessions_lock 只创建一次。
_sessions_lock: asyncio.Lock | None = None
_sessions_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_sessions_lock() -> asyncio.Lock:
    """获取绑定当前事件循环的锁（惰性创建 + loop 变化检测）。"""
    global _sessions_lock, _sessions_lock_loop
    loop = asyncio.get_running_loop()
    if _sessions_lock is None or _sessions_lock_loop is not loop:
        _sessions_lock = asyncio.Lock()
        _sessions_lock_loop = loop
    return _sessions_lock


async def get_session(thread_id: str) -> TaskSession | None:
    """异步获取会话"""
    return await _get_session_store().get(thread_id)


async def pop_session(thread_id: str) -> TaskSession | None:
    """异步移除会话（供 stream 的 finally 使用）"""
    return await _get_session_store().pop(thread_id)


async def set_session(thread_id: str, session: TaskSession) -> None:
    """写入会话"""
    await _get_session_store().set(thread_id, session)


async def cleanup_stale_sessions() -> int:
    """
    定期清理过期会话。

    ✅ 已由 app.py 的 FastAPI startup 事件中的后台循环定期调用。
    """
    return await _get_session_store().cleanup_stale(getattr(_hub.dev, "_SESSION_TTL", 3600))


async def acquire_session(
    thread_id: str,
    resume: bool,
) -> TaskSession:
    """
    获取或创建 session。

    resume=True 时：复用已有 session，重置 cancel_event（若已 set）。
    resume=False 时：无条件新建（覆盖旧 session）。
    """
    async with _get_sessions_lock():
        if resume:
            existing = await _get_session_store().get(thread_id)
            if existing is not None:
                if existing.cancel_event.is_set():
                    existing.cancel_event.clear()
                logger.info("[Agent] resume 复用已有 session | thread=%s", thread_id)
                return existing
            logger.warning(
                "[Agent] resume=True 但无既有 session，走新建路径 | thread=%s",
                thread_id,
            )

        session = TaskSession(thread_id)
        await _get_session_store().set(thread_id, session)
        return session


__all__ = [
    "TaskSession",
    "get_session",
    "pop_session",
    "set_session",
    "acquire_session",
    "cleanup_stale_sessions",
]
