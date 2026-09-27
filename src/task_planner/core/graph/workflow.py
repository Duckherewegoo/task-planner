"""
workflow.py — LangGraph 工作流定义（异步优化版）
"""
from __future__ import annotations

import asyncio
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
import atexit
import weakref

_sqlite_connections: list[weakref.ref] = []
logger = get_logger(__name__)


def _cleanup_sqlite_connections():
    """进程退出时关闭所有 SQLite checkpoint 连接"""
    for ref in _sqlite_connections:
        conn = ref()
        if conn is not None:
            try:
                conn.close()
                logger.debug("[Workflow] SQLite checkpoint connection closed")
            except Exception:
                pass
    _sqlite_connections.clear()


atexit.register(_cleanup_sqlite_connections)


def _build_checkpointer():
    cp_type = os.getenv("CHECKPOINTER_TYPE", "memory").lower()

    if cp_type == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver
        import sqlite3

        db_path = os.getenv("CHECKPOINT_DB_PATH", "checkpoints.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        # ✅ 注册弱引用，避免阻止 GC 回收
        _sqlite_connections.append(weakref.ref(conn))

        return SqliteSaver(conn)

    elif cp_type == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        conn_str = os.getenv("CHECKPOINT_POSTGRES_URL", "")
        if not conn_str:
            raise ValueError("CHECKPOINTER_TYPE=postgres 但未设置 CHECKPOINT_POSTGRES_URL")
        return PostgresSaver.from_conn_string(conn_str)

    else:
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


def build_graph():
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
                _graph_instance = build_graph()
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
) -> dict[str, Any]:
    """
    异步恢复历史任务。

    注意：此函数会调用 graph.ainvoke 来继续执行，
    但实际执行由上层（agent）控制，这里只返回恢复后的状态。
    如果需要持续流式输出，请使用 agent.run_task_stream 的 resume 模式。
    """
    g = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = g.get_state(config)
    if not snapshot or not snapshot.values:
        raise ValueError(f"未找到 thread_id={thread_id} 的历史状态")

    logger.info("[Workflow] resume_graph_async: thread=%s, action=%s", thread_id, user_action)

    feedback: dict[str, Any] = {"action": user_action}
    if user_action == "modify" and modified_input:
        feedback["user_input"] = modified_input
    if target_node_index is not None:
        feedback["target_node_index"] = target_node_index

    # 更新状态注入反馈
    g.update_state(config, feedback, as_node="execute")

    # 获取最新状态
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


# 保留同步版本（兼容旧代码，内部调用异步）
# workflow.py — 替换原有的 resume_graph / get_thread_state

def resume_graph(thread_id: str, **kwargs) -> dict[str, Any]:
    """
    同步版本（兼容旧代码）。
    ⚠️ 在已有事件循环的异步环境中请使用 resume_graph_async()
    """
    try:
        return asyncio.run(resume_graph_async(thread_id, **kwargs))
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e):
            # 降级：复用当前事件循环（注意：不支持嵌套，仅用于简单同步场景）
            logger.warning(
                "[Workflow] resume_graph() 在运行中的事件循环内被调用，"
                "请迁移至 await resume_graph_async()。本次使用 run_until_complete 降级执行。"
            )
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(resume_graph_async(thread_id, **kwargs))
        raise


def get_thread_state(thread_id: str) -> dict[str, Any] | None:
    """同步版本（兼容旧代码），同上降级策略"""
    try:
        return asyncio.run(get_thread_state_async(thread_id))
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e):
            logger.warning(
                "[Workflow] get_thread_state() 在运行中的事件循环内被调用，"
                "请迁移至 await get_thread_state_async()。"
            )
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(get_thread_state_async(thread_id))
        raise


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
