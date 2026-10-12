"""
workflow.py — LangGraph 工作流定义（异步优化版）
═══════════════════════════════════════════════════════════════════════
目标运行时：Python 3.14+

Changelog:
  ── v1 ──
  ✅ checkpointer 资源 atexit 统一清理
  ✅ graph 单例懒加载（线程安全）
  ✅ _LazyGraphProxy 支持同步/异步 API

  ── v2 ──
  ✅ P1-1：修复 _run_sync 报错信息中的 async 名推导 bug。
  ✅ P1-2：_graph_instance / _graph_lock 提到模块顶部。
  ✅ P1-3：_LazyGraphProxy 加白名单（后改黑名单）。
  ✅ P1-4：atexit 用 sentinel 防重复注册。
  ✅ P1-5：_cleanup_checkpointers 简化 close 分支。
  ✅ P2-1：resume_graph_async / get_thread_state_async 补齐 "next_nodes"。

  ── v3 ──
  ✅ Patch A（state.py）：needs_planning 默认值改为 True。
  ✅ Patch B：_ATEXIT_REGISTERED 挂 sys，抗 importlib.reload。
  ✅ Patch C：_cleanup_checkpointers 失败日志从 debug 提到 warning。
  ✅ Patch D：_force_rebuild_graph 重建前先清旧资源，消除累积泄漏。
  ✅ Patch E：_LazyGraphProxy 改黑名单 + __repr__ 去硬编码。
  ✅ Patch F：resume_graph_async —— validate_user_action 校验入口；
               feedback 键名与 TaskState 字段对齐；新增
               resume_from_node_index 参数；同步图操作走 asyncio.to_thread。
  ✅ Patch G：get_thread_state_async —— 异常上抛；next 转 list；
               去掉无信息量的 has_checkpoint。
  ✅ Patch H：_run_sync 传 factory，避免协程未 await 的 RuntimeWarning。
  ✅ Patch I：build_graph docstring 加废弃提示。
  ✅ Patch F-补：图构建走 to_thread，避免首次调用阻塞事件循环。
  ✅ Patch F-补：resume_from_node_index 与 target_node_index 同时非空时
               显式报错。

  ── v4 ──
  ✅ Patch J：resume_graph_async docstring 明确契约——
              本函数**只更新状态，不续跑图**。调用方拿到 next_nodes 后
              必须自行 `g.astream(None, config)` 让图继续执行。
              这是当前最容易被误解的点：函数名"resume"暗示"一调就恢复"，
              但 LangGraph 的 update_state 语义是"只写状态不推进"。
              不改名（会破坏现有调用方），但把隐含契约显式化。
  ✅ Patch K：顶部 checkpointer 迁移说明补一句——异步 checkpointer
              的资源释放需要 await，atexit 无法 await。迁移时必须在
              ASGI lifespan shutdown 里 await _cleanup_checkpointers_async()。
  ✅ Patch L：feedback["user_action"] 显式 str(action)——
              避免异步 checkpointer 反序列化时丢失枚举类型，
              导致 routes.py 的 isinstance assert 失败。

设计说明：
  本文件是"图的生命周期与断点续传"这一个主题的多侧面，
  属于高内聚单职责，按 utils/ 同策略保持单文件，不做拆分。

⚠️ checkpointer 现状：
   目前为同步版（Memory / Sqlite / Postgres）。
   async API 通过 asyncio.to_thread 把同步 IO 放进线程池，
   立即获得正确的异步语义，不需要先迁移 checkpointer。

   未来迁移到 AsyncSqliteSaver / AsyncPostgresSaver 时的完整清单：
     1. 把 `await asyncio.to_thread(g.get_state, config)`
        机械替换为 `await g.aget_state(config)`；
        `g.update_state` → `await g.aupdate_state`。
     2. 把 atexit 注册的 `_cleanup_checkpointers` 替换为
        在 ASGI lifespan shutdown 里 `await _cleanup_checkpointers_async()`。
        ⚠️ atexit 无法 await——异步 saver 的资源不会被同步清理释放，
           进程退出时会看到 ResourceWarning 或连接被 GC 强制断开。
     3. _build_checkpointer 换成异步工厂，在图首次构建时 await 建立连接。

⚠️ 与 execute.py 的契约变更：
   resume_graph_async 传出的 feedback 键名从 "action" / "target_node_index"
   改为 "user_action" / "retry_node_id" / "resume_from_node_index"。
   execute.py 里 `feedback.get("action")` 必须同步改为
   `feedback.get("user_action")`。
"""
import asyncio
import atexit
import os
import sys
import threading
from typing import Any, Callable, Coroutine

from langgraph.graph import END, START, StateGraph

from task_planner.core.graph.nodes import (
    cancel_node,
    direct_answer_node,
    execute_node,
    intent_node,
    plan_node,
    refine_node,
    render_node,
    route_after_execute,
    route_after_intent,
    route_after_render,
    save_node,
)
from task_planner.core.graph.state import TaskState, UserAction, validate_user_action
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════
#  单例状态
# ══════════════════════════════════════════════════
_graph_instance: Any | None = None
_graph_lock = threading.Lock()


# ══════════════════════════════════════════════════
#  Checkpointer 资源管理
# ══════════════════════════════════════════════════
#  设计说明：
#    CHECKPOINTER_TYPE / CHECKPOINT_DB_PATH / CHECKPOINT_POSTGRES_URL
#    直接从环境变量读，不进 cog。原因：
#      - 这些是 LangGraph 框架层的运行时配置，非应用业务配置
#      - 运维/部署关心，不应该出现在 config/schema.yaml 的用户可见项里
#      - 未来若要动态化，再加一个 cog section 也不迟
_sqlite_connections: list[Any] = []
_postgres_savers: list[Any] = []


def _cleanup_checkpointers() -> None:
    """进程退出时关闭所有 checkpoint 资源。

    ⚠️ 本函数只处理同步 checkpointer。异步 saver 的 close 是协程，
       atexit 无法 await——迁移到异步 checkpointer 时必须改用
       ASGI lifespan shutdown 里的 async 版本。
    """
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
            logger.warning("[Workflow] PostgresSaver close failed: %s", e)
    _postgres_savers.clear()

    for conn in _sqlite_connections:
        try:
            conn.close()
            logger.debug("[Workflow] SQLite checkpoint connection closed")
        except Exception as e:
            logger.warning("[Workflow] SQLite close failed: %s", e)
    _sqlite_connections.clear()


# ✅ Patch B：flag 挂 sys，模块被 reload 时不会重置
if not getattr(sys, "_task_planner_atexit_registered", False):
    atexit.register(_cleanup_checkpointers)
    sys._task_planner_atexit_registered = True


def _build_checkpointer():
    """根据 CHECKPOINTER_TYPE 环境变量构建 checkpointer（同步版）"""
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
    builder.add_node("refine", refine_node)
    builder.add_node("save", save_node)
    builder.add_node("render", render_node)
    builder.add_node("execute", execute_node)
    builder.add_node("direct_answer", direct_answer_node)
    builder.add_node("cancel", cancel_node)

    def _route_start(state: TaskState) -> str:
        # Patch A 后 needs_planning 默认 True，新任务必进 intent。
        # fallback 也用 True 保持一致。
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


def get_graph() -> Any:
    """同步获取 graph 实例（线程安全懒加载）。

    ⚠️ 首次调用会执行 StateGraph.compile()，可能阻塞数十毫秒。
       异步上下文请用 `await asyncio.to_thread(get_graph)`。
    """
    global _graph_instance
    if _graph_instance is not None:
        return _graph_instance
    with _graph_lock:
        if _graph_instance is None:
            _graph_instance = _build_graph()
    return _graph_instance


def build_graph():
    """
    兼容旧名。语义已是单例：多次调用返回同一实例。
    ⚠️ Patch I：名字误导，新代码请用 get_graph()。未来版本将删除。
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
        # ✅ Patch D：先清旧资源，防累积泄漏
        _cleanup_checkpointers()
        _graph_instance = _build_graph()
    return _graph_instance


# ══════════════════════════════════════════════════
#  Lazy 代理
# ══════════════════════════════════════════════════
class _LazyGraphProxy:
    """
    代理对象：转发属性访问与调用到真实 graph 实例。

    ✅ Patch E：改为黑名单——只拦本模块私有成员，其他属性（含所有
       dunder 协议如 __aenter__ / __await__ / __iter__）全部转发，
       让 `async with graph:` / `await graph` / `for x in graph` 正常工作。
    """

    # 只拦本模块的私有成员，避免 graph._graph_instance 之类误触真图
    _DENY_ATTRS = frozenset({
        "_graph_instance",
        "_graph_lock",
        "_build_graph",
        "_force_rebuild_graph",
    })

    def __getattr__(self, name: str):
        if name in self._DENY_ATTRS:
            raise AttributeError(
                f"_LazyGraphProxy 不允许访问 {name!r}；"
                f"请直接调用 get_graph() 获取真实实例"
            )
        return getattr(get_graph(), name)

    def __call__(self, *args, **kwargs):
        return get_graph()(*args, **kwargs)

    def __repr__(self) -> str:
        if _graph_instance is not None:
            return f"<LazyGraphProxy → {type(_graph_instance).__name__} (initialized)>"
        # ✅ Patch E：不再硬编码 "CompiledGraph"
        return "<LazyGraphProxy → (not yet initialized)>"


graph: Any = _LazyGraphProxy()


# ══════════════════════════════════════════════════
#  异步断点续传 API
# ══════════════════════════════════════════════════
async def resume_graph_async(
    thread_id: str,
    user_action: str = "continue",
    modified_input: str | None = None,
    target_node_index: int | None = None,
    resume_from_node_index: int | None = None,
    as_node: str | None = None,
) -> dict[str, Any]:
    """
    更新 thread 的状态，为"续跑"做准备。

    ⚠️ Patch J：本函数**只更新状态，不续跑图**。
       函数名"resume"容易让人以为一调就恢复——但 LangGraph 的
       `update_state` 语义是"只写状态、不推进图"。本函数返回后，
       图仍停在原来的 interrupt / checkpoint 处。

       调用方拿到返回值后，**必须再执行一次**：
           async for _ in g.astream(None, config, stream_mode="values"):
               ...
       或：
           await g.ainvoke(None, config)
       才能让图真正续跑。

       为什么不做自动续跑：调用方需要自己控制 stream_mode、
       错误处理、进度回调——自动续跑会把这些责任抢过来，
       反而降低灵活性。

    Args:
        thread_id:               会话 ID
        user_action:             用户动作 "continue" / "modify" / "cancel" / "retry_node"
        modified_input:          action=modify 时携带的新输入
        target_node_index:       action=retry_node 时指定要重试的节点索引
        resume_from_node_index:  从 checkpoint 恢复时指定起始节点索引
        as_node:                 指定"这次状态更新由哪个节点产生"。
                                 None（推荐）时由 LangGraph 自动从 checkpoint.next 推断。

    Returns:
        {
            "thread_id": str,
            "state": dict,        # 更新后的 state 快照
            "next_nodes": list,   # 待执行节点（供调用方决定是否续跑）
        }

    Raises:
        ValueError: thread_id 无历史状态 / user_action 非法 /
                    target_node_index 与 resume_from_node_index 同时非空
    """
    # ✅ Patch F：校验入口，非法值立即报错
    action = validate_user_action(user_action)

    # ✅ Patch F-补：与 state.py 三指针优先级保持一致，拒绝模糊调用
    if target_node_index is not None and resume_from_node_index is not None:
        raise ValueError(
            "target_node_index（retry_node）与 resume_from_node_index 语义冲突，"
            "二者只能传一个。按 state.py 声明，retry_node_id 优先级更高，"
            "请调用方明确意图后只传一个。"
        )

    # ✅ Patch F-补：首次调用可能 build 图，走 to_thread 避免阻塞 loop
    g = await asyncio.to_thread(get_graph)
    config = {"configurable": {"thread_id": thread_id}}

    # ✅ Patch F：同步 checkpointer 用 to_thread 避免阻塞事件循环。
    # 未来 checkpointer 异步化后，机械替换为 await g.aget_state(config)
    snapshot = await asyncio.to_thread(g.get_state, config)
    if not snapshot or not snapshot.values:
        raise ValueError(f"未找到 thread_id={thread_id} 的历史状态")

    logger.info(
        "[Workflow] resume_graph_async: thread=%s, action=%s, as_node=%s",
        thread_id, action, as_node or "<auto>",
    )

    # ✅ Patch F：键名与 TaskState 字段对齐
    # ✅ Patch L：显式 str(action)——checkpointer 反序列化不保证还原枚举，
    # 下游 routes.py 的 isinstance(UserAction) 断言可能失败。
    # StrEnum 的 str() 返回值本身就是 .value（'modify' 等），语义无损。
    feedback: dict[str, Any] = {"user_action": str(action)}
    if action == UserAction.MODIFY and modified_input:
        feedback["modified_input"] = modified_input
    if target_node_index is not None:
        feedback["retry_node_id"] = target_node_index
    if resume_from_node_index is not None:
        feedback["resume_from_node_index"] = resume_from_node_index

    if as_node is None:
        await asyncio.to_thread(g.update_state, config, feedback)
    else:
        await asyncio.to_thread(g.update_state, config, feedback, as_node=as_node)

    new_snapshot = await asyncio.to_thread(g.get_state, config)
    return {
        "thread_id": thread_id,
        "state": new_snapshot.values if new_snapshot else {},
        # ✅ Patch G：snapshot.next 是 tuple，返回前转 list
        "next_nodes": list(new_snapshot.next) if new_snapshot else [],
    }


async def get_thread_state_async(thread_id: str) -> dict[str, Any] | None:
    """
    异步获取指定 thread 的 checkpoint 状态。

    Returns:
        找到：{"thread_id": str, "state": dict, "next_nodes": list}
        未找到：None
    Raises:
        Exception: 底层存储访问失败（区别于"未找到"）
    """
    # ✅ Patch F-补：首次调用可能 build 图，走 to_thread
    g = await asyncio.to_thread(get_graph)
    config = {"configurable": {"thread_id": thread_id}}

    # ✅ Patch G：异常上抛，不再把"错"伪装成"无"
    snapshot = await asyncio.to_thread(g.get_state, config)
    if not snapshot or not snapshot.values:
        return None
    return {
        "thread_id": thread_id,
        "state": snapshot.values,
        # ✅ Patch G：tuple → list
        "next_nodes": list(snapshot.next),
    }


# ══════════════════════════════════════════════════
#  同步兼容层
# ══════════════════════════════════════════════════
def _run_sync(
    coro_factory: Callable[[], Coroutine],
    sync_api_name: str,
    async_api_name: str,
):
    """
    在同步上下文中运行协程；在已有 loop 时立即报错而非死循环。

    ✅ Patch H：传入 factory 而非 coroutine——
       避免错误用法下创建出永不 await 的协程对象、触发 RuntimeWarning。
    ✅ P1-1：显式传入 async_api_name，不再靠字符串替换推导。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 当前线程无运行中的 loop → 安全
        return asyncio.run(coro_factory())

    raise RuntimeError(
        f"{sync_api_name}() 被在运行中的 event loop 内调用。"
        f"请改用 `await {async_api_name}(...)`。"
    )


def resume_graph(thread_id: str, **kwargs) -> dict[str, Any]:
    """
    同步版本（仅用于没有 event loop 的同步上下文）。

    ⚠️ 与 async 版一样：本函数只更新状态，不续跑图。
       调用方拿到返回值后需自行驱动图继续执行。

    ⚠️ 异步环境请用: await resume_graph_async(thread_id, ...)
    """
    # ✅ Patch H：传 factory
    return _run_sync(
        lambda: resume_graph_async(thread_id, **kwargs),
        sync_api_name="resume_graph",
        async_api_name="resume_graph_async",
    )


def get_thread_state(thread_id: str) -> dict[str, Any] | None:
    """
    同步版本（仅用于没有 event loop 的同步上下文）。

    ⚠️ 异步环境请用: await get_thread_state_async(thread_id)
    """
    # ✅ Patch H：传 factory
    return _run_sync(
        lambda: get_thread_state_async(thread_id),
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
