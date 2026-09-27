"""
agent.py — 异步 Agent 主入口（LangGraph 异步驱动版）

支持两种执行模式：
  - 新建任务: await run_task_stream(user_input, thread_id)
  - 断点续传: await run_task_stream("", thread_id, resume=True)
    从 LangGraph checkpoint 恢复状态并继续 stream

所有 I/O 操作均异步，取消信号通过 asyncio.Event 传递。
会话管理使用 asyncio.Lock，保证协程安全。
"""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any, AsyncGenerator, Optional

from task_planner.utils.context import cancel_event_var
from task_planner.core.graph.nodes import _sanitize_input
from langgraph.types import Command
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.core.graph.workflow import graph
from task_planner.core.graph.state import TaskState, UserAction
from task_planner.services.view_model import FRONTEND_FIELDS
from task_planner.infrastructure.config import SKIP_PLANNING_PATTERNS, SESSION_TTL
from task_planner.infrastructure.session_store import InMemorySessionStore

_session_store: SessionStore = InMemorySessionStore()

logger = get_logger(__name__)
_SESSION_TTL = SESSION_TTL
_SKIP_PLANNING_PATTERNS = SKIP_PLANNING_PATTERNS

# ================================================================
#  活跃任务管理（协程安全）
# ================================================================

_active_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = asyncio.Lock()


class TaskSession:
    """单个任务会话 - 完全异步化"""

    def __init__(self, thread_id: str, user_input: str):
        self.thread_id = thread_id
        self.user_input = user_input

        # ✅ 异步事件，用于取消信号
        self.cancel_event = asyncio.Event()
        self.start_time = time.time()
         # ✅ 有界队列，最多缓存 100 条进度；满了就丢弃最老的
        self.progress_q: asyncio.Queue = asyncio.Queue(maxsize=100)

        self._runner_task: Optional[asyncio.Task] = None

        # ✅ config 保持不变，LangGraph 兼容
        self.config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
            }
        }

    async def emit(self, msg: str) -> None:
        try:
            self.progress_q.put_nowait(msg)
        except asyncio.QueueFull:
            # 丢弃最老的一条，保证新消息能进
            try:
                self.progress_q.get_nowait()
                self.progress_q.put_nowait(msg)
            except asyncio.QueueEmpty:
                pass
        logger.info("[Agent] %s", msg)

    async def cancel_task(thread_id: str) -> bool:
        session = await get_session(thread_id)
        if not session:
            return False
        session.cancel_event.set()
        if session._runner_task and not session._runner_task.done():
            session._runner_task.cancel()   # ✅ 强制打断阻塞的 await
        return True


    async def get_session(thread_id: str):
        return await _session_store.get(thread_id)

    async def cleanup_stale_sessions() -> int:
        return await _session_store.cleanup_stale(_SESSION_TTL)

# ================================================================
#  初始状态构建
# ================================================================

def _should_skip_planning(user_input: str) -> bool:
    """显式拒绝规划的正则拦截（同步，纯计算）"""
    for pattern in _SKIP_PLANNING_PATTERNS:
        if pattern.search(user_input):
            logger.info("[Agent] 显式拒绝规划命中: %s", pattern.pattern)
            return True
    return False


def _make_initial_state(user_input: str, thread_id: str) -> TaskState:
    """构建初始状态，含显式拒绝规划拦截"""
    skip_planning = _should_skip_planning(user_input)

    if skip_planning:
        logger.info("[Agent] 关键词/正则拦截：用户明确要求跳过规划")

    return TaskState(
        messages=[],
        user_input=user_input,
        thread_id=thread_id,
        intent={},
        needs_planning=not skip_planning,
        plan={},
        nodes=[],
        edges=[],
        refined_nodes=[],
        task_id="",
        current_node_index=0,
        node_results=[],
        svg="",
        direct_response="__DIRECT_RESPONSE_PENDING__" if skip_planning else "",
        cancel_requested=False,
        error="",
        status_text="",
        steps=["跳过规划：用户显式拒绝"] if skip_planning else [],
        user_action=None,
        modified_input=None,
        retry_node_id=None,
    )


# ================================================================
#  修改：复用已有 session（尤其 resume 场景）
# ================================================================

async def _acquire_session(
    thread_id: str,
    user_input: str,
    resume: bool,
) -> TaskSession:
    """获取或创建 session；resume 时复用旧 session 的 cancel_event。"""
    async with _sessions_lock:
        entry = _active_sessions.get(thread_id)

        if resume and entry:
            session: TaskSession = entry["session"]
            # 复用旧 cancel_event，避免覆盖导致下游丢失信号
            # 但如果旧 event 已被 set（上一次取消过），需要新建一个
            if session.cancel_event.is_set():
                session.cancel_event = asyncio.Event()
            logger.info("[Agent] resume 复用已有 session | thread=%s", thread_id)
            return session

        # 新建或覆盖
        session = TaskSession(thread_id, user_input)
        _active_sessions[thread_id] = {
            "session": session,
            "start_time": session.start_time,
        }
        return session

# ================================================================
#  核心异步流式执行
# ================================================================

async def run_task_stream(user_input, thread_id, enable_refine=True, resume=False):
    if not isinstance(thread_id, str):
        raise TypeError(...)

    rid = set_req_id()
    logger.info("[Agent] 启动任务 | thread=%s req=%s", thread_id, rid)

    session = await _acquire_session(thread_id, user_input, resume)
    session._runner_task = asyncio.current_task()

    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)

    try:
        event_count = 0
        if resume:
            stream_iter = graph.astream(None, session.config,
                                         stream_mode=["values", "updates"])
        else:
            initial_state = _make_initial_state(user_input, thread_id)
            stream_iter = graph.astream(dict(initial_state), session.config,
                                         stream_mode=["values", "updates"])

        async for event in stream_iter:
            event_count += 1
            mode, data = event if isinstance(event, tuple) else ("values", event)
            if session.cancel_event.is_set():
                logger.info("[Agent] 收到取消信号 (req=%s)", rid)
                break
            snap = _extract_snapshot(data, session, mode=mode)
            if snap is not None:
                yield snap
            if _is_graph_finished(data, mode):
                break

    except asyncio.CancelledError:
        session.cancel_event.set()
        logger.warning("[Agent] 任务被外部取消 (req=%s)", rid)
        yield {"type": "cancelled", "task_id": thread_id}
        raise
    except Exception as exc:
        logger.error("[Agent] 异常: %s\n%s", exc, traceback.format_exc())
        yield {"type": "error", "error": str(exc), "task_id": thread_id}
    finally:
        session.cancel_event.set()
        await _session_store.pop(thread_id)
        logger.info("[Agent] session 清理完成 | thread=%s req=%s", thread_id, rid)

# ================================================================
#  异步命令操作（取消 / 修改 / 继续 / 重试）
# ================================================================

async def cancel_task(thread_id: str) -> bool:
    """取消任务（异步）"""
    session = await get_session(thread_id)
    if session:
        session.cancel_event.set()
        logger.info("[Agent] 取消信号已发送 | thread=%s", thread_id)
        return True
    return False


async def _resume_with_command(
    session: TaskSession,
    resume_data: dict[str, Any],
) -> None:
    """
    异步 resume 执行入口，使用 graph.ainvoke 进行恢复。
    确保 ContextVar 已设置，使节点能感知取消。
    """
    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)

    try:
        await graph.ainvoke(Command(resume=resume_data), session.config)
    finally:
        try:
            cancel_event_var.reset(token)
        except ValueError:
            pass


async def modify_task(thread_id: str, new_input: str) -> bool:
    """修改任务需求（异步）"""
    session = await get_session(thread_id)
    if not session:
        return False

    try:
        cleaned = _sanitize_input(new_input)
    except ValueError as e:
        logger.warning("[Agent] 修改输入校验失败: %s", e)
        return False

    await _resume_with_command(session, {
        "action": UserAction.MODIFY,
        "user_input": cleaned,
    })
    return True


async def resume_task(thread_id: str) -> bool:
    """继续执行（异步）"""
    session = await get_session(thread_id)
    if not session:
        return False

    await _resume_with_command(session, {
        "action": UserAction.CONTINUE,
    })
    return True


async def retry_node_cmd(thread_id: str, node_id: int) -> bool:
    """重试指定节点（异步）"""
    session = await get_session(thread_id)
    if not session:
        return False

    await _resume_with_command(session, {
        "action": UserAction.RETRY_NODE,
        "node_id": node_id,
    })
    return True


# ================================================================
#  内部工具（同步，无 I/O）
# ================================================================

def _extract_snapshot(event, session, mode="values"):
    if mode == "updates":
        return None
    view = {k: event.get(k) for k in FRONTEND_FIELDS}
    view.update({
        "type": "progress",
        "task_id": event.get("task_id", ""),
        "elapsed": round(time.time() - session.start_time, 1),
        "nodes": event.get("nodes") or [],
        "edges": event.get("edges") or [],
    })
    return view

def _is_graph_finished(event_data: dict[str, Any], mode: str) -> bool:
    """
    轻量级完成判断（同步，纯函数）。
    - 取消/错误 → 结束
    - 节点索引超出范围 → 结束
    - 直接回答（且无节点）→ 结束
    - resume 模式不依赖 direct_response 判断。
    """
    if mode != "values":
        return False

    if event_data.get("cancel_requested"):
        return True
    if event_data.get("error"):
        return True

    nodes = event_data.get("nodes", [])
    idx = event_data.get("current_node_index", 0)
    if nodes and idx >= len(nodes):
        return True

    direct_resp = event_data.get("direct_response", "")
    if direct_resp and direct_resp != "__DIRECT_RESPONSE_PENDING__":
        if not nodes:
            return True

    return False
