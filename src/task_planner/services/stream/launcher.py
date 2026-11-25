"""launcher.py — 启动/恢复后台流式任务"""

import asyncio
from collections.abc import Callable
from typing import Any

from task_planner.infrastructure.logger_setup import get_logger

from .cleaner import state_cleaner
from .state import TaskStreamState

logger = get_logger("task_planner.stream")


async def _run_worker(
    thread_id: str,
    run_fn: Callable,
    run_kwargs: dict[str, Any],
) -> str:
    """公共 worker：在 uvicorn 的持久 loop 上创建 TaskStreamState 并跑 run_fn。

    - 流内每个 snapshot 推给 state
    - 终止事件（complete / cancelled / timeout / error / paused）触发 mark_finished
      或 mark_paused
    - 生成器正常结束（无终止事件）也视为完成，保险

    ⚠️ FastAPI backend 下本协程直接 await 即可：uvicorn 持有持久 loop，
       asyncio.create_task 创建的任务跨回调存活，不需要独立的常驻 loop。
    """
    from task_planner.services.agent import commands as _commands

    state = TaskStreamState(thread_id)
    await state_cleaner.register(thread_id, state)

    async def _worker():
        try:
            logger.info("[StreamMgr] 后台协程启动 | thread=%s", thread_id)
            async for snapshot in run_fn(**run_kwargs):
                if state.cancelled:
                    logger.info("[StreamMgr] 收到取消 | thread=%s", thread_id)
                    break

                await state.push(snapshot)

                snap_type = snapshot.get("type", "")
                if snap_type == "paused":
                    logger.info(
                        "[StreamMgr] 图暂停，等待用户交互 | thread=%s next=%s",
                        thread_id,
                        snapshot.get("next_nodes"),
                    )
                    await state.mark_paused(next_nodes=snapshot.get("next_nodes", []))
                    return
                if snap_type in ("complete", "cancelled", "timeout", "error"):
                    err = snapshot.get("message") or snapshot.get("error")
                    await state.mark_finished(error=err or None)
                    return
            await state.mark_finished()
            logger.info("[StreamMgr] 后台协程结束（无终止事件）| thread=%s", thread_id)
        except asyncio.CancelledError:
            logger.info("[StreamMgr] 后台协程被取消 | thread=%s", thread_id)
            await state.mark_cancelled()
        except Exception as e:
            logger.error("[StreamMgr] 后台协程异常 | thread=%s err=%s", thread_id, e)
            await state.mark_finished(error=str(e))
        finally:
            _commands.unregister_abort(thread_id)

    task = asyncio.create_task(_worker())
    state.set_task(task)
    _commands.register_abort(thread_id, state.abort_worker)
    return thread_id


async def start_stream(
    thread_id: str,
    user_input: str,
    enable_refine: bool,
    run_task_stream_fn: Callable,
) -> str:
    """启动后台流式任务，返回 thread_id。

    执行模型：TaskStreamState 与 create_task 都在调用方所在的 uvicorn
    loop 上创建，保证 Motor / asyncio 原语不跨 loop。
    """
    run_kwargs = {
        "user_input": user_input,
        "thread_id": thread_id,
        "enable_refine": enable_refine,
        "resume": False,
    }
    return await _run_worker(thread_id, run_task_stream_fn, run_kwargs)


async def resume_stream(
    thread_id: str,
    user_action: str = "continue",
    modified_input: str | None = None,
    retry_node_id: int | str | None = None,
    run_task_stream_fn: Callable | None = None,
) -> str:
    """从 checkpoint 恢复任务并启动后台流式执行。

    单一推进路径：
      构造 feedback dict → 传给 run_task_stream(resume=True, resume_data=feedback)
      run_task_stream 内部用 astream(Command(resume=feedback)) 一次推进图。

    ⚠️ 与 commands.py 的分工：
      commands.py 走 graph.ainvoke(Command(...)) —— 同步阻塞，面向 API/UI 的
      直接命令（不推送流式进度）。
      launcher.py 走 run_task_stream(resume=True) —— 流式推送，面向 stream 层。

      两者不能同时用于同一 thread。本函数内部不再调用 resume_graph_async
      （旧实现会双重推进：resume_graph_async 一次 + run_task_stream 一次）。

    ⚠️ 返回值语义：
      - "paused"    → 图停在下一个 interrupt，等待用户交互
      - "complete"  → 图执行到 END
      - "error"     → 图内部抛异常
      - "cancelled" → 用户主动取消
    """
    from task_planner.services.agent import run_task_stream as _default_run

    existing = await state_cleaner.get(thread_id)
    if existing and not existing.finished:
        await state_cleaner.unregister(thread_id)
        logger.info("[StreamMgr] 覆盖已完成的旧任务 %s", thread_id)
    elif existing and not existing.finished:
        raise RuntimeError(
            f"任务 {thread_id} 仍在运行中，无法 resume。请先调用 cancel_stream() 或等待其完成。"
        )

    feedback: dict[str, Any] = {"user_action": user_action}
    if modified_input is not None:
        feedback["modified_input"] = modified_input
    if retry_node_id is not None:
        feedback["retry_node_id"] = retry_node_id

    logger.info(
        "[StreamMgr] resume_stream: thread=%s action=%s retry_node_id=%s",
        thread_id,
        user_action,
        retry_node_id,
    )

    run_fn = run_task_stream_fn or _default_run
    run_kwargs = {
        "user_input": "",
        "thread_id": thread_id,
        "enable_refine": False,
        "resume": True,
        "resume_data": feedback,
    }
    return await _run_worker(thread_id, run_fn, run_kwargs)


__all__ = ["start_stream", "resume_stream"]
