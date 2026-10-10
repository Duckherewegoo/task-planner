"""
agent.py — 异步 Agent 主入口（LangGraph 异步驱动版）

支持两种执行模式：
  - 新建任务: await run_task_stream(user_input, thread_id)
  - 断点续传: await run_task_stream("", thread_id, resume=True)
    从 LangGraph checkpoint 恢复状态并继续 stream

所有 I/O 操作均异步，取消信号通过 asyncio.Event 传递。
会话管理使用 SessionStore + asyncio.Lock，保证协程安全。
"""
from __future__ import annotations

import asyncio
import contextvars
import time
import traceback
from typing import Any, AsyncGenerator, Optional

from langgraph.types import Command

from task_planner.core.graph.nodes import sanitize_input as _sanitize_input
from task_planner.core.graph.state import TaskState, UserAction
from task_planner.core.graph.workflow import graph
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.infrastructure.regexes import SKIP_PLANNING_PATTERNS
from task_planner.services.view_model import FRONTEND_FIELDS
from task_planner.utils.context import cancel_event_var

SESSION_TTL = _hub.dev.SESSION_TTL
# ✅ P0-2 修复：同时导入 Protocol 和实现
from task_planner.infrastructure.session_store import (InMemorySessionStore,
                                                       SessionStore)

logger = get_logger(__name__)
_SESSION_TTL = SESSION_TTL
_SKIP_PLANNING_PATTERNS = SKIP_PLANNING_PATTERNS


# ================================================================
#  会话存储（唯一权威来源）
# ================================================================
# ✅ P0-4 修复：删除 _active_sessions，只保留 _session_store。
#    之前的 _acquire_session 写 _active_sessions、而 get_session 读 _session_store，
#    两套机制数据永不交叉 → 取消失败 + 会话泄漏。
_session_store: SessionStore = InMemorySessionStore()
_sessions_lock = asyncio.Lock()   # 用于 _acquire_session 的原子性


# ================================================================
#  会话类
# ================================================================

class TaskSession:
    """单个任务会话 - 完全异步化"""

    def __init__(self, thread_id: str, user_input: str):
        self.thread_id = thread_id
        self.user_input = user_input

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
            try:
                self.progress_q.get_nowait()
                self.progress_q.put_nowait(msg)
            except asyncio.QueueEmpty:
                pass
        logger.info("[Agent] %s", msg)


# ================================================================
#  会话获取 / 清理（模块级函数）
# ✅ P0-3 修复：从 TaskSession 类内部移出，改为模块级
# ================================================================

async def get_session(thread_id: str) -> Optional[TaskSession]:
    """异步获取会话"""
    return await _session_store.get(thread_id)


async def cleanup_stale_sessions() -> int:
    """定期清理过期会话（可由后台任务调用）"""
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


def _make_initial_state(user_input: str, thread_id: str) -> dict[str, Any]:
    """构建初始状态，含显式拒绝规划拦截"""
    skip_planning = _should_skip_planning(user_input)

    if skip_planning:
        logger.info("[Agent] 关键词/正则拦截：用户明确要求跳过规划")

    # ✅ P1-2 修复：TypedDict 用 dict 字面量构造；补 schema_version 字段
    return {
        "messages": [],
        "user_input": user_input,
        "thread_id": thread_id,
        "intent": {},
        "needs_planning": not skip_planning,
        "plan": {},
        "nodes": [],
        "edges": [],
        "refined_nodes": [],
        "task_id": "",
        "current_node_index": 0,
        "node_results": [],
        "svg": "",
        "flowchart_html": "",
        "direct_response": "__DIRECT_RESPONSE_PENDING__" if skip_planning else "",
        "cancel_requested": False,
        "error": "",
        "status_text": "",
        "steps": ["跳过规划：用户显式拒绝"] if skip_planning else [],
        "user_action": None,
        "modified_input": None,
        "retry_node_id": None,
        "resume_from_node_index": None,
        "schema_version": 1,
    }


# ================================================================
#  获取或创建 session（resume 场景复用 cancel_event）
# ================================================================

async def _acquire_session(
    thread_id: str,
    user_input: str,
    resume: bool,
) -> TaskSession:
    """获取或创建 session；resume 时复用旧 session 的 cancel_event。"""
    # ✅ P0-4 修复：读写统一走 _session_store
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


# ================================================================
#  核心异步流式执行
# ================================================================

async def run_task_stream(
    user_input: str,
    thread_id: str,
    enable_refine: bool = True,
    resume: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    异步生成器，实时推送任务执行进度。
    调用方应使用 `async for event in run_task_stream(...):` 消费。
    """
    if not isinstance(thread_id, str):
        raise TypeError(
            f"thread_id must be str, got {type(thread_id).__name__}: {thread_id!r}"
        )

    rid = set_req_id()
    logger.info("[Agent] 启动任务 | thread=%s req=%s", thread_id, rid)

    session = await _acquire_session(thread_id, user_input, resume)
    session._runner_task = asyncio.current_task()

    # ✅ P0-1 修复：contextvars 已 import
    # 用 copy_context 绑定，避免跨 Task 恢复时 token 语义错位
    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)

    try:
        event_count = 0

        if resume:
            logger.info("[Agent] resume 模式 | thread=%s", thread_id)
            stream_iter = graph.astream(
                None,
                session.config,
                stream_mode=["values", "updates"],
            )
        else:
            initial_state = _make_initial_state(user_input, thread_id)
            logger.info("[Agent] initial_state keys: %s", list(initial_state.keys()))
            stream_iter = graph.astream(
                initial_state,
                session.config,
                stream_mode=["values", "updates"],
            )

        async for event in stream_iter:
            event_count += 1

            if isinstance(event, tuple):
                mode, data = event
                logger.info("[Agent] event #%d [%s] received", event_count, mode)
            else:
                mode, data = "values", event
                logger.info("[Agent] event #%d received", event_count)

            if session.cancel_event.is_set():
                logger.info("[Agent] 收到取消信号 (req=%s)", rid)
                break

            snapshot = _extract_snapshot(data, session, mode=mode)
            if snapshot is not None:
                yield snapshot

            if _is_graph_finished(data, mode):
                break

        logger.info("[Agent] graph.astream() 结束，共 %d 个事件", event_count)

    except asyncio.CancelledError:
        session.cancel_event.set()
        logger.warning("[Agent] 任务被外部取消 (req=%s)", rid)
        yield {"type": "cancelled", "task_id": thread_id}
        raise

    except Exception as exc:
        logger.error("[Agent] 异常: %s\n%s", exc, traceback.format_exc())
        yield {
            "type": "error",
            "error": str(exc),
            "task_id": thread_id,
        }
    finally:
        session.cancel_event.set()
        # ✅ P0-4 修复：pop 也走 _session_store
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
        if session._runner_task and not session._runner_task.done():
            session._runner_task.cancel()
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
    # ✅ P0-5 修复：删除 finally 里对未定义 token 的引用。
    # 用 copy_context 绑定即可，无需 reset。
    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)

    await graph.ainvoke(Command(resume=resume_data), session.config)


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

def _extract_snapshot(
    event: dict[str, Any],
    session: TaskSession,
    mode: str = "values",
) -> Optional[dict[str, Any]]:
    """从 graph event 中提取前端需要的快照"""
    if mode == "updates":
        return None

    elapsed = time.time() - session.start_time
    view = {k: event.get(k) for k in FRONTEND_FIELDS}
    view.update({
        "type": "progress",
        "task_id": event.get("task_id", ""),
        "elapsed": round(elapsed, 1),
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
