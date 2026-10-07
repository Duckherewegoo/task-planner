"""launcher.py — 启动/恢复后台流式任务"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from task_planner.core.graph.workflow import (
    get_thread_state_async,
    resume_graph_async,
)
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
    使用 asyncio.create_task 在事件循环中运行异步生成器。
    """
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
                # for...else：循环自然结束（未 break）→ 流跑完没遇到终止事件
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


async def resume_stream(
    thread_id: str,
    user_action: str = "continue",
    modified_input: str | None = None,
    target_node_index: int | None = None,
    run_task_stream_fn: Callable | None = None,
) -> str:
    """从 LangGraph checkpoint 恢复历史任务并启动后台流式执行"""
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

    state = TaskStreamState(thread_id)
    await state_cleaner.register(thread_id, state)

    try:
        await resume_graph_async(
            thread_id=thread_id,
            user_action=user_action,
            modified_input=modified_input,
            target_node_index=target_node_index,
        )
    except Exception as e:
        logger.error("[StreamMgr] resume_graph 失败: %s", e)
        await state.mark_finished(error=str(e))
        return thread_id

    run_fn = run_task_stream_fn or _default_run

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


__all__ = ["start_stream", "resume_stream"]
