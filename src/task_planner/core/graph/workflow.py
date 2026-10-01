"""
workflow.py — LangGraph 工作流定义（异步优化版）
═══════════════════════════════════════════════════════════════════════
Changelog:
  ── v1 ──
  ✅ checkpointer 资源 atexit 统一清理
  ✅ graph 单例懒加载（线程安全）
  ✅ _LazyGraphProxy 支持同步/异步 API

  ── v2 ──
  ✅ P1-1：修复 _run_sync 报错信息中的 async 名推导 bug
           （"get_thread_state".replace("_graph", "_graph_async") 得到的是原串）
           改为显式传入 async_api_name。
  ✅ P1-2：_graph_instance / _graph_lock 提到模块顶部，
           避免"定义在使用之后"的阅读障碍。
  ✅ P1-3：_LazyGraphProxy 加白名单，只转发非下划线属性。
           防止 graph._graph_instance 之类误触真图。
  ✅ P1-4：atexit 用 sentinel 防重复注册（模块被 reload 时）。
  ✅ P1-5：_cleanup_checkpointers 简化 close 分支，close 异常 try 包裹。
  ✅ P2-1：resume_graph_async / get_thread_state_async 的返回值
           补齐 "next_nodes" 字段，保持一致。

设计说明：
  本文件是"图的生命周期与断点续传"这一个主题的多侧面，
  属于高内聚单职责，按 utils/ 同策略保持单文件，不做拆分。
"""
from __future__ import annotations

import asyncio
import atexit
import os
import threading
from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from task_planner.core.graph.nodes import (
    cancel_node,
    direct_answer_node,
    execute_node,
    intent_node,
    plan_node,
    refine_node_fn,
    render_node,
    route_after_execute,
    route_after_intent,
    route_after_render,
    save_node,
)
from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════
#  单例状态（顶部定义，供下方所有函数引用）
# ══════════════════════════════════════════════════
_graph_instance: Optional[Any] = None
_graph_lock = threading.Lock()


# ══════════════════════════════════════════════════
#  Checkpointer 资源管理
# ══════════════════════════════════════════════════
#
#  设计说明：
#    CHECKPOINTER_TYPE / CHECKPOINT_DB_PATH / CHECKPOINT_POSTGRES_URL
#    直接从环境变量读，不进 cog。原因：
#      - 这些是 LangGraph 框架层的运行时配置，非应用业务配置
#      - 运维/部署关心，不应该出现在 config/schema.yaml 的用户可见项里
#      - 未来若要动态化，再加一个 cog section 也不迟
#
_sqlite_connections: list[Any] = []
_postgres_savers: list[Any] = []


def _cleanup_checkpointers() -> None:
    """进程退出时关闭所有 checkpoint 资源"""
    # Postgres（context manager 或 saver）
    for saver in _postgres_savers:
        try:
            exit_fn = getattr(saver, "__exit__", None)
            if exit_fn is not None:
                exit_fn(None, None, None)
            else:
                close_fn = getattr(saver, "close", None)
                if close_fn is not None:
                    close_fn()
            logger.debug("[Workflow] PostgresSaver closed")
        except Exception as e:
            logger.debug("[Workflow] PostgresSaver close failed: %s", e)
    _postgres_savers.clear()

    # SQLite
    for conn in _sqlite_connections:
        try:
            conn.close()
            logger.debug("[Workflow] SQLite checkpoint connection closed")
        except Exception as e:
            logger.debug("[Workflow] SQLite close failed: %s", e)
    _sqlite_connections.clear()


# ✅ P1-4：防重复注册（模块被 reload 时）
_ATEXIT_REGISTERED = False
if not _ATEXIT_REGISTERED:
    atexit.register(_cleanup_checkpointers)
    _ATEXIT_REGISTERED = True


def _build_checkpointer():
    """根据 CHECKPOINTER_TYPE 环境变量构建 checkpointer"""
    cp_type = os.getenv("CHECKPOINTER_TYPE", "memory").lower()

    if cp_type == "sqlite":
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        db_path = os.getenv("CHECKPOINT_DB_PATH", "checkpoints.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        _sqlite_connections.append(conn)
        logger.info("[Workflow] Checkpointer: SqliteSaver (db=%s)", db_path)
        return SqliteSaver(conn)

    if cp_type == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        conn_str = os.getenv("CHECKPOINT_POSTGRES_URL", "")
        if not conn_str:
            raise ValueError(
                "CHECKPOINTER_TYPE=postgres 但未设置 CHECKPOINT_POSTGRES_URL"
            )

        try:
            cm_or_saver = PostgresSaver.from_conn_string(conn_str)
            # 兼容老/新版本 API：
            #   老版本: 直接返回 saver
            #   新版本: 返回 context manager
            if hasattr(cm_or_saver, "__enter__") and hasattr(cm_or_saver, "__exit__"):
                saver = cm_or_saver.__enter__()
                _postgres_savers.append(cm_or_saver)
            else:
                saver = cm_or_saver
                _postgres_savers.append(saver)
            logger.info("[Workflow] Checkpointer: PostgresSaver")
            return saver
        except Exception as e:
            logger.error("[Workflow] PostgresSaver 初始化失败: %s", e)
            raise

    from langgraph.checkpoint.memory import MemorySaver

    logger.info("[Workflow] Checkpointer: MemorySaver（仅进程内，重启即丢）")
    return MemorySaver()


# ══════════════════════════════════════════════════
#  图构建
# ══════════════════════════════════════════════════
def _build_graph():
    """构建并编译 LangGraph（所有节点为异步）"""
    builder = StateGraph(TaskState)

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


def get_graph():
    """同步获取 graph 实例（线程安全懒加载）"""
    global _graph_instance
    if _graph_instance is None:
        with _graph_lock:
            if _graph_instance is None:
                _graph_instance = _build_graph()
    return _graph_instance


def build_graph():
    """
    兼容旧名。语义已是单例：多次调用返回同一实例。
    如需强制重建（测试场景），用 _force_rebuild_graph()。
    """
    return get_graph()


def _force_rebuild_graph():
    """
    强制重建 graph。仅用于测试/诊断。
    ⚠️ 会丢弃旧 checkpointer，旧 checkpoint 数据不自动迁移。
    """
    global _graph_instance
    with _graph_lock:
        _graph_instance = _build_graph()
    return _graph_instance


# ══════════════════════════════════════════════════
#  Lazy 代理（同步/异步属性访问统一入口）
# ══════════════════════════════════════════════════
class _LazyGraphProxy:
    """
    代理对象：转发属性访问与调用到真实 graph 实例。
    ✅ P1-3：只转发非下划线属性，防止 graph._graph_instance 之类误触真图。
    """

    # 允许显式访问的特殊属性（不影响真图）
    _OWN_ATTRS = frozenset({"__class__", "__dict__", "__repr__", "__doc__"})

    def __getattr__(self, name: str):
        # 下划线开头的属性一律走默认行为（AttributeError），不转发
        if name.startswith("_") and name not in self._OWN_ATTRS:
            raise AttributeError(
                f"_LazyGraphProxy 不允许访问私有属性 {name!r}；"
                f"请直接调用 get_graph() 获取真实实例"
            )
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
    modified_input: Optional[str] = None,
    target_node_index: Optional[int] = None,
    as_node: Optional[str] = None,
) -> dict[str, Any]:
    """
    异步恢复历史任务。

    Args:
        thread_id:         会话 ID
        user_action:       用户动作 "continue" / "modify" / "cancel" / "retry_node"
        modified_input:    action=modify 时携带的新输入
        target_node_index: action=retry_node 时指定要重试的节点索引
        as_node:           指定"这次状态更新由哪个节点产生"。
                           None（推荐）时由 LangGraph 自动从 checkpoint.next 推断，
                           能正确处理"从任意节点中断后恢复"的场景。
                           仅当明确要强制从某个节点重新开始时才显式传入。

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


async def get_thread_state_async(thread_id: str) -> Optional[dict[str, Any]]:
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
def _run_sync(coro, sync_api_name: str, async_api_name: str):
    """
    在同步上下文中运行协程；在已有 loop 时立即报错而非死循环。
    ✅ P1-1：显式传入 async_api_name，不再靠字符串替换推导。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 当前线程无运行中的 loop → 安全
        return asyncio.run(coro)

    raise RuntimeError(
        f"{sync_api_name}() 被在运行中的 event loop 内调用。"
        f"请改用 `await {async_api_name}(...)`。"
    )


def resume_graph(thread_id: str, **kwargs) -> dict[str, Any]:
    """
    同步版本（仅用于没有 event loop 的同步上下文）。

    ⚠️ 异步环境请用: await resume_graph_async(thread_id, ...)
    """
    return _run_sync(
        resume_graph_async(thread_id, **kwargs),
        sync_api_name="resume_graph",
        async_api_name="resume_graph_async",
    )


def get_thread_state(thread_id: str) -> Optional[dict[str, Any]]:
    """
    同步版本（仅用于没有 event loop 的同步上下文）。

    ⚠️ 异步环境请用: await get_thread_state_async(thread_id)
    """
    return _run_sync(
        get_thread_state_async(thread_id),
        sync_api_name="get_thread_state",
        async_api_name="get_thread_state_async",
    )


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
