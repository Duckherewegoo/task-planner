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

⚠️ 关于 SessionStore 的可替换性：
   当前硬编码 InMemorySessionStore——单进程部署没问题。
   多 worker 部署时必须替换为 RedisSessionStore（项目里已存在），
   否则每个 worker 各有一份内存池，用户"继续任务"可能路由到
   另一个 worker 而找不到 session。
   替换点：本文件顶部 _session_store 的初始化。
   TODO: 从环境变量或 cog 读 SESSION_STORE_TYPE，工厂化创建。

⚠️ 关于会话清理：
   cleanup_stale_sessions 需要一个后台任务定期调用——
   当前没有调用方（潜在内存泄漏）。建议在 ASGI lifespan startup
   里 create_task 一个循环。
   TODO: 加后台清理任务。
"""

import asyncio
import time
from typing import Any

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.session_store import InMemorySessionStore, SessionStore

logger = get_logger(__name__)

_SESSION_TTL = _hub.dev.SESSION_TTL


# ═══════════════════════════════════════════════════════════════════
#  TaskSession
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
#  会话池（全局单例）
# ═══════════════════════════════════════════════════════════════════
# ⚠️ 单进程内存实现——多 worker 部署需替换为 RedisSessionStore。
#    见模块 docstring 的"关于 SessionStore 的可替换性"。
_session_store: SessionStore = InMemorySessionStore()

# 惰性创建 + loop 绑定检测：
#    asyncio.Lock 在 Python 3.10+ 首次 await 时绑定当前 loop，
#    跨 loop 复用会抛 RuntimeError。这里检测 loop 变化后重建，
#    让测试/CLI 场景（进程内多 loop）不崩。
#    生产单 loop 场景下，_sessions_lock 只创建一次。
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
    return await _session_store.get(thread_id)


async def pop_session(thread_id: str) -> TaskSession | None:
    """异步移除会话（供 stream 的 finally 使用）"""
    return await _session_store.pop(thread_id)


async def set_session(thread_id: str, session: TaskSession) -> None:
    """写入会话"""
    await _session_store.set(thread_id, session)


async def cleanup_stale_sessions() -> int:
    """
    定期清理过期会话。

    ⚠️ 当前无后台任务调用——潜在内存泄漏。
       应在 ASGI lifespan startup 里 create_task 一个循环定期跑。
       见模块 docstring 的"关于会话清理"。
    """
    return await _session_store.cleanup_stale(_SESSION_TTL)


async def acquire_session(
    thread_id: str,
    resume: bool,
) -> TaskSession:
    """
    获取或创建 session。

    resume=True 时：复用已有 session，重置 cancel_event（若已 set）。
    resume=False 时：无条件新建（覆盖旧 session）。

    ⚠️ 参数 user_input 已删——它过去只用于 TaskSession.__init__ 的
       一个未读取字段。当前所有调用方传的 user_input 都用不到。
    """
    async with _get_sessions_lock():
        if resume:
            existing = await _session_store.get(thread_id)
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
