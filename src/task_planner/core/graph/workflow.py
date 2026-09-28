"""
workflow.py — LangGraph 工作流定义（异步优化版）
"""
from __future__ import annotations

import asyncio
import atexit
import os
import threading
from typing import Any

from langgraph.graph import END, START, StateGraph

from task_planner.core.graph.nodes import (
    cancel_node,
    direct_answer_node,
    execute_node,
    intent_node,
    plan_node,
    save_node,
    refine_node_fn,
    render_node,
    route_after_execute,
    route_after_intent,
    route_after_render,
)
from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════
#  Checkpointer 资源管理
# ══════════════════════════════════════════════════

# ✅ 修复 P1：强引用列表。这些连接本来就应该活到进程退出，
#    用弱引用反而可能被 GC 提前回收，导致 Saver 拿到已关闭的连接。
_sqlite_connections: list[Any] = []
_postgres_savers: list[Any] = []


def _cleanup_checkpointers():
    """进程退出时关闭所有 checkpoint 资源"""
    # 关闭 Postgres（如果是 context manager 型）
    for saver in _postgres_savers:
        try:
            close = getattr(saver, "close", None) or getattr(saver, "__exit__", None)
            if close is not None:
                if getattr(saver, "__exit__", None) is not None:
                    saver.__exit__(None, None, None)
                else:
                    close()
                logger.debug("[Workflow] PostgresSaver closed")
        except Exception as e:
            logger.debug("[Workflow] PostgresSaver close failed: %s", e)
    _postgres_savers.clear()

    # 关闭 SQLite
    for conn in _sqlite_connections:
        try:
            conn.close()
            logger.debug("[Workflow] SQLite checkpoint connection closed")
        except Exception:
            pass
    _sqlite_connections.clear()


atexit.register(_cleanup_checkpointers)


def _build_checkpointer():
    """根据 CHECKPOINTER_TYPE 环境变量构建 checkpointer"""
    cp_type = os.getenv("CHECKPOINTER_TYPE", "memory").lower()

    if cp_type == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver
        import sqlite3

        db_path = os.getenv("CHECKPOINT_DB_PATH", "checkpoints.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        # ✅ 修复 P1：强引用，防止被 GC 提前回收
        _sqlite_connections.append(conn)

        return SqliteSaver(conn)

    elif cp_type == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        conn_str = os.getenv("CHECKPOINT_POSTGRES_URL", "")
        if not conn_str:
            raise ValueError(
                "CHECKPOINTER_TYPE=postgres 但未设置 CHECKPOINT_POSTGRES_URL"
            )

        # ✅ 修复 P2：PostgresSaver.from_conn_string 返回 context manager，
        #    需要显式 __enter__ 才能拿到可用的 saver；同时把它登记到强引用列表，
        #    由 atexit 统一 __exit__。
        #
        # ⚠️ 注意：不同 langgraph 版本 API 有差异：
        #    - 老版本: PostgresSaver.from_conn_string(conn_str) 直接返回 saver
        #    - 新版本: 返回 context manager
        # 这里用 try/except 兼容两种情况。
        try:
            cm_or_saver = PostgresSaver.from_conn_string(conn_str)
            # 判断是不是 context manager
            if hasattr(cm_or_saver, "__enter__") and hasattr(cm_or_saver, "__exit__"):
                saver = cm_or_saver.__enter__()
                _postgres_savers.append(cm_or_saver)  # 用 cm 来持有，退出时调 __exit__
            else:
                saver = cm_or_saver
                _postgres_savers.append(saver)
            return saver
        except Exception as e:
            logger.error("[Workflow] PostgresSaver 初始化失败: %s", e)
            raise

    else:
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


# ══════════════════════════════════════════════════
#  图构建
# ══════════════════════════════════════════════════

def _build_graph():
    """构建并编译 LangGraph（所有节点为异步）"""
    builder = StateGraph(TaskState)

    # 添加异步节点
    builder.add_node("intent", intent_node)
    builder.add_node("plan", plan_node)
    builder.add_node("refine", refine_node_fn)
    builder.add_node("save", save_node)
    builder.add_node("render", render_node)
    builder.add_node("execute", execute_node)
    builder.add_node("direct_answer", direct_answer_node)
    builder.add_node("cancel", cancel_node)

    def _route_start(state: TaskState) -> str:
        if not state.get("needs_planning", True):
            return "direct_answer"
        return "intent"

    builder.add_conditional_edges(
        START,
        _route_start,
        {"direct_answer": "direct_answer", "intent": "intent"},
    )

    builder.add_conditional_edges(
        "intent",
        route_after_intent,
        {"plan": "plan", "direct_answer": "direct_answer", "cancel": "cancel"},
    )

    builder.add_edge("plan", "refine")
    builder.add_edge("refine", "save")
    builder.add_edge("save", "render")

    builder.add_conditional_edges(
        "render",
        route_after_render,
        {"execute": "execute"},
    )

    builder.add_conditional_edges(
        "execute",
        route_after_execute,
        {
            "execute": "execute",
            "intent": "intent",
            "finish": END,
            "cancel": "cancel",
        },
    )

    builder.add_edge("direct_answer", END)
    builder.add_edge("cancel", END)

    checkpointer = _build_checkpointer()
    return builder.compile(checkpointer=checkpointer)


# ✅ 修复 P1：保留 build_graph 作为兼容入口，但内部走单例，避免重复构建导致
#    checkpointer / DB 连接泄漏。
def build_graph():
    """
    获取（或首次构建）编译后的图。
    ⚠️ 已改为单例语义：多次调用返回同一个实例，不再是"每次新构建"。
    如需强制重建（例如测试中），请使用 _force_rebuild_graph()。
    """
    return get_graph()


def _force_rebuild_graph():
    """
    强制重建 graph 实例。仅用于测试/诊断场景。
    ⚠️ 会丢弃旧的 checkpointer，旧的 checkpoint 数据不会自动迁移。
    """
    global _graph_instance
    with _graph_lock:
        _graph_instance = _build_graph()
    return _graph_instance


# ══════════════════════════════════════════════════
#  懒加载单例（线程安全）
# ══════════════════════════════════════════════════

_graph_instance: Any = None
_graph_lock = threading.Lock()


def get_graph():
    """同步获取 graph 实例（线程安全懒加载）"""
    global _graph_instance
    if _graph_instance is None:
        with _graph_lock:
            if _graph_instance is None:
                _graph_instance = _build_graph()
    return _graph_instance


class _LazyGraphProxy:
    """代理对象，支持属性访问和调用（同步/异步均可）"""

    def __getattr__(self, name: str):
        return getattr(get_graph(), name)

    def __call__(self, *args, **kwargs):
        return get_graph()(*args, **kwargs)

    def __repr__(self) -> str:
        if _graph_instance is not None:
            return f"<LazyGraphProxy → {type(_graph_instance).__name__} (initialized)>"
        return "<LazyGraphProxy → CompiledGraph (not yet initialized)>"


graph: Any = _LazyGraphProxy()


# ══════════════════════════════════════════════════
#  异步断点续传 API
# ══════════════════════════════════════════════════

async def resume_graph_async(
    thread_id: str,
    user_action: str = "continue",
    modified_input: str | None = None,
    target_node_index: int | None = None,
    as_node: str | None = None,
) -> dict[str, Any]:
    """
    异步恢复历史任务。

    Args:
        thread_id: 会话 ID
        user_action: 用户动作，如 "continue" / "modify" / "cancel" / "retry_node"
        modified_input: action=modify 时携带的新输入
        target_node_index: action=retry_node 时指定要重试的节点索引
        as_node: ✅ 新增。指定"这次状态更新由哪个节点产生"。
                 None（推荐）时由 LangGraph 自动从 checkpoint.next 推断，
                 能正确处理"从任意节点中断后恢复"的场景。
                 只有当你明确要强制从某个节点重新开始时，才显式传入。

    Returns:
        {
            "thread_id": str,
            "state": dict,
            "next_nodes": list,
        }
    """
    g = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = g.get_state(config)
    if not snapshot or not snapshot.values:
        raise ValueError(f"未找到 thread_id={thread_id} 的历史状态")

    logger.info(
        "[Workflow] resume_graph_async: thread=%s, action=%s, as_node=%s",
        thread_id, user_action, as_node or "<auto>",
    )

    feedback: dict[str, Any] = {"action": user_action}
    if user_action == "modify" and modified_input:
        feedback["user_input"] = modified_input
    if target_node_index is not None:
        feedback["target_node_index"] = target_node_index

    # ✅ 修复 P0-2：不再硬编码 as_node="execute"。
    #    硬编码会导致"从 refine / intent 阶段中断后恢复"时路由错乱，
    #    丢失中间所有已细化的节点。None 时由 LangGraph 自动从 checkpoint.next 推断。
    if as_node is None:
        g.update_state(config, feedback)
    else:
        g.update_state(config, feedback, as_node=as_node)

    new_snapshot = g.get_state(config)
    return {
        "thread_id": thread_id,
        "state": new_snapshot.values if new_snapshot else {},
        "next_nodes": new_snapshot.next if new_snapshot else [],
    }


async def get_thread_state_async(thread_id: str) -> dict[str, Any] | None:
    """异步获取指定 thread 的 checkpoint 状态"""
    g = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = g.get_state(config)
        if not snapshot or not snapshot.values:
            return None
        return {
            "thread_id": thread_id,
            "state": snapshot.values,
            "next_nodes": snapshot.next,
            "has_checkpoint": True,
        }
    except Exception as e:
        logger.warning("[Workflow] get_thread_state_async failed: %s", e)
        return None


# ══════════════════════════════════════════════════
#  同步兼容层
# ══════════════════════════════════════════════════
#
# ✅ 修复 P0-1：删除了原先的 `loop.run_until_complete(...)` 降级逻辑。
#
# 原因：在"已经运行中的 event loop"里调用 run_until_complete 会立即再抛
#       RuntimeError: This event loop is already running。
#       这个降级路径 100% 无效，只会把一个异常替换成另一个异常。
#
# 现在的行为：
#   - 当前线程无运行中的 loop → 正常执行（asyncio.run）
#   - 当前线程有运行中的 loop → 抛明确错误，提示调用方改用 await 版本
#
# 项目整体已是 asyncio 架构，同步 API 仅为兼容极少数遗留调用。
# ══════════════════════════════════════════════════

def _run_sync(coro, api_name: str):
    """在同步上下文中运行协程；在已有 loop 时立即报错而非死循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 当前线程没有运行中的 loop → 安全
        return asyncio.run(coro)

    # 当前线程已有运行中的 loop → 明确报错
    raise RuntimeError(
        f"{api_name}() 被在运行中的 event loop 内调用。"
        f"请改用 `await {api_name.replace('_graph', '_graph_async')}(...)` "
        f"或直接 `await {api_name}_async(...)`。"
    )


def resume_graph(thread_id: str, **kwargs) -> dict[str, Any]:
    """
    同步版本（仅用于没有 event loop 的同步上下文）。

    ⚠️ 在异步环境中（FastAPI 请求处理、Jupyter、异步测试）请使用:
        await resume_graph_async(thread_id, ...)
    """
    return _run_sync(resume_graph_async(thread_id, **kwargs), "resume_graph")


def get_thread_state(thread_id: str) -> dict[str, Any] | None:
    """
    同步版本（仅用于没有 event loop 的同步上下文）。

    ⚠️ 在异步环境中请使用: await get_thread_state_async(thread_id)
    """
    return _run_sync(get_thread_state_async(thread_id), "get_thread_state")


# ══════════════════════════════════════════════════
#  公开 API
# ══════════════════════════════════════════════════

__all__ = [
    "build_graph",
    "get_graph",
    "resume_graph",
    "resume_graph_async",
    "get_thread_state",
    "get_thread_state_async",
    "graph",
]
