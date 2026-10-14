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
    单个任务会话 - 完全异步化
    ⚠️ runner task 的生命周期**不归本类管**——归 stream 层的
       TaskStreamState（通过 set_task / abort_worker）。
       TaskSession 只负责"用户可见的会话状态"（cancel_event / progress_q）。
       历史上曾在此暴露 attach_runner / cancel_runner——已删除，
       避免与 TaskStreamState 双轨。
    """

    # ✅ __weakref__ 显式加进 slots——否则 weakref.ref(session) 抛 TypeError。
    #    未来 SessionStore 想用 WeakValueDictionary 自动 GC 无引用会话时，
    #    这一步是前提。
    __slots__ = (
        "__weakref__",
        "cancel_event",
        "config",
        "progress_q",
        "start_time",
        "thread_id",
        "user_input",
    )

    def __init__(self, thread_id: str, user_input: str):
        self.thread_id = thread_id
        self.user_input = user_input

        self.cancel_event = asyncio.Event()
        self.start_time = time.time()

        # 有界队列，最多缓存 100 条进度；满了丢最老
        self.progress_q: asyncio.Queue = asyncio.Queue(maxsize=100)

        # LangGraph 兼容
        self.config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id}
        }


    async def emit(self, msg: str) -> None:
        """
        向进度队列推一条消息。

        ⚠️ 当前实现无 await——签名是 async 是为了未来扩展
           （如切换到 Redis pub/sub 或写 DB）而不改调用方。
           如果你确认永远不会变，可以改成 def emit(...)，
           但会让现有 `await session.emit(...)` 需要同步删除 await。

        队列满时：丢最老、塞最新——保证 UI 拿到的是"当前状态"
        而不是"已经过时的最早几条"。
        """
        try:
            self.progress_q.put_nowait(msg)
        except asyncio.QueueFull:
            try:
                self.progress_q.get_nowait()
                self.progress_q.put_nowait(msg)
            except asyncio.QueueEmpty:
                # 理论不可达：put_nowait 刚因 QueueFull 失败说明队列非空。
                # 保底防御——不因竞争条件抛出。
                pass
        logger.info("[Agent] %s", msg)


# ═══════════════════════════════════════════════════════════════════
#  会话池（全局单例）
# ═══════════════════════════════════════════════════════════════════
# ⚠️ 单进程内存实现——多 worker 部署需替换为 RedisSessionStore。
#    见模块 docstring 的"关于 SessionStore 的可替换性"。
_session_store: SessionStore = InMemorySessionStore()

# ✅ 惰性创建 + loop 绑定检测：
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
    user_input: str,
    resume: bool,
) -> TaskSession:
    """
    获取或创建 session。

    resume=True 时：
      - 若有既有 session → 复用它，并重置 cancel_event（若已 set）
      - 若无 → 走新建路径（可能是进程重启后 session 已丢，
        或 thread_id 拼错）——宽容处理，让用户从新任务开始，
        同时打 warning 便于排查

    resume=False 时：
      - 无条件新建（覆盖旧 session，如果存在）

    ⚠️ cancel_event 重置用 clear() 而非替换对象：
       commands.cancel_task / runner task / contextvar 可能持有旧 event
       引用——替换对象会让它们"监听的是被丢弃的 event"。
       clear() 保持引用不变，所有持有者看到同一状态。

    ⚠️ 锁粒度：整个"查询 + 创建"在 _sessions_lock 内完成——
       单进程场景保证原子性。多 worker + RedisSessionStore 场景下
       本锁不跨进程，需要 store 层提供原子 create-or-get。
       见模块 docstring 的"关于 SessionStore 的可替换性"。
    """
    async with _get_sessions_lock():
        if resume:
            existing = await _session_store.get(thread_id)
            if existing is not None:
                if existing.cancel_event.is_set():
                    # ✅ 用 clear() 保持引用稳定——
                    #    不替换对象，让所有持有者看到同一 event 的状态变化。
                    existing.cancel_event.clear()
                logger.info("[Agent] resume 复用已有 session | thread=%s", thread_id)
                return existing
            # resume=True 但无既有 session——可能是进程重启后用户点"继续"，
            # 也可能是 thread_id 拼错。记录 warning 便于排查。
            logger.warning(
                "[Agent] resume=True 但无既有 session，走新建路径 | thread=%s",
                thread_id,
            )

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
