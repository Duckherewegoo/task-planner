"""launcher.py — 启动/恢复后台流式任务"""

import asyncio
from collections.abc import Callable

from task_planner.core.graph.workflow import get_thread_state_async, resume_graph_async
from task_planner.infrastructure.async_runtime import submit
from task_planner.infrastructure.logger_setup import get_logger

from .cleaner import state_cleaner
from .state import TaskStreamState

logger = get_logger("task_planner.stream")


async def start_stream(
    thread_id: str,
    user_input: str,
    enable_refine: bool,
    run_task_stream_fn: Callable,
) -> str:
    """
    启动后台流式任务，返回 thread_id。

    ⚠️ 执行模型（2026-10-14 修复）：
       Dash 回调通过 asyncio.run() 驱动——每次回调一个新 loop，回调返回即毁。
       原实现用 asyncio.create_task(_worker()) 在当前 loop 上挂后台任务——
       task 会在回调返回后立即被取消。

       现改为：
         1. 把 _bootstrap 整个协程提交到常驻 loop（async_runtime）
         2. _bootstrap 里创建 TaskStreamState、注册 state_cleaner、
            create_task(_worker)——所有 asyncio 原语都在常驻 loop 上
            首次执行，无跨 loop 绑定
         3. 当前 loop 用 asyncio.wrap_future 等 _bootstrap 完成拿
            thread_id（bootstrap 只做构造，很快返回）
    """
    async def _bootstrap() -> str:
        """在常驻 loop 上执行——所有 asyncio 原语都在这首次创建。"""
        state = TaskStreamState(thread_id)
        await state_cleaner.register(thread_id, state)

        async def _worker():
            try:
                logger.info("[StreamMgr] 后台协程启动 | thread=%s", thread_id)
                async for snapshot in run_task_stream_fn(
                    user_input, thread_id, enable_refine
                ):
                    if state.cancelled:
                        logger.info("[StreamMgr] 收到取消 | thread=%s", thread_id)
                        break

                    await state.push(snapshot)

                    snap_type = snapshot.get("type", "")
                    if snap_type in ("complete", "cancelled", "timeout", "error"):
                        err = snapshot.get("message") or snapshot.get("error")
                        await state.mark_finished(error=err or None)
                        break
                else:
                    await state.mark_finished()

                logger.info("[StreamMgr] 后台协程结束 | thread=%s", thread_id)
            except asyncio.CancelledError:
                logger.info("[StreamMgr] 后台协程被取消 | thread=%s", thread_id)
                await state.mark_cancelled()
            except Exception as e:
                logger.error("[StreamMgr] 后台协程异常 | thread=%s err=%s", thread_id, e)
                await state.mark_finished(error=str(e))

        task = asyncio.create_task(_worker())
        state.set_task(task)
        return thread_id

    future = submit(_bootstrap())
    return await asyncio.wrap_future(future)


async def resume_stream(
    thread_id: str,
    user_action: str = "continue",
    modified_input: str | None = None,
    target_node_index: int | None = None,
    run_task_stream_fn: Callable | None = None,
) -> str:
    """从 LangGraph checkpoint 恢复历史任务并启动后台流式执行。"""
    from task_planner.services.agent import run_task_stream as _default_run

    existing = await state_cleaner.get(thread_id)
    if existing and not existing.finished:
        logger.warning("[StreamMgr] resume_stream: 任务仍在运行中 %s", thread_id)
        raise RuntimeError(
            f"任务 {thread_id} 仍在运行中，无法 resume。"
            f"请先调用 cancel_stream() 或等待其完成。"
        )

    cp_state = await get_thread_state_async(thread_id)
    if not cp_state:
        raise ValueError(f"未找到 thread_id={thread_id} 的 checkpoint，无法恢复")

    logger.info(
        "[StreamMgr] resume_stream: thread=%s action=%s", thread_id, user_action
    )

    try:
        await resume_graph_async(
            thread_id=thread_id,
            user_action=user_action,
            modified_input=modified_input,
            target_node_index=target_node_index,
        )
    except Exception as e:
        logger.error("[StreamMgr] resume_graph 失败: %s", e)
        raise

    run_fn = run_task_stream_fn or _default_run

    async def _bootstrap() -> str:
        """在常驻 loop 上执行。"""
        state = TaskStreamState(thread_id)
        await state_cleaner.register(thread_id, state)

        async def _worker():
            try:
                logger.info("[StreamMgr] resume 后台协程启动 | thread=%s", thread_id)
                async for snapshot in run_fn(
                    user_input="",
                    thread_id=thread_id,
                    enable_refine=False,
                    resume=True,
                ):
                    if state.cancelled:
                        break
                    await state.push(snapshot)

                    snap_type = snapshot.get("type", "")
                    if snap_type in ("complete", "cancelled", "timeout", "error"):
                        err = snapshot.get("message") or snapshot.get("error")
                        await state.mark_finished(error=err or None)
                        break
                else:
                    await state.mark_finished()
            except asyncio.CancelledError:
                logger.info("[StreamMgr] resume 协程被取消 | thread=%s", thread_id)
                await state.mark_cancelled()
            except Exception as e:
                logger.error("[StreamMgr] resume 异常 | thread=%s err=%s", thread_id, e)
                await state.mark_finished(error=str(e))

        task = asyncio.create_task(_worker())
        state.set_task(task)
        return thread_id

    future = submit(_bootstrap())
    return await asyncio.wrap_future(future)


__all__ = ["start_stream", "resume_stream"]
