"""launcher.py — 启动/恢复后台流式任务"""

import asyncio
from collections.abc import Callable
from typing import Any

from task_planner.infrastructure.logger_setup import get_logger
from task_planner.services.agent.commands import release_thread_lock

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

    ⚠️ 取消与清理：
      - abort 注册在 create_task 之后（state.set_task 已设好，abort_worker 可用）
      - unregister_abort / release_thread_lock 在 finally 中完成，不会泄漏
      - 协作取消（state.cancelled）走 mark_cancelled，不走 mark_finished
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
                    await state.mark_cancelled()  # ← 改这里：直接标记取消并退出
                    return

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
            # 生成器正常结束（无终止事件）视为完成
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
            _commands.release_thread_lock(thread_id)

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
    existing = await state_cleaner.get(thread_id)
    if existing and not existing.finished:
        raise RuntimeError(f"任务 {thread_id} 已在运行中")

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

    行为概览
    --------
    1. 检查 thread_id 是否已有注册的任务：
       - 已完成（finished=True）→ 清理 resume lock + state 注册，直接覆盖
       - 运行中（finished=False）→ 先 cancel_task 干净退出（硬取消 + 协作取消
         + 清理 abort/lock），再覆盖启动新任务
       - 不存在 → 直接启动
    2. 构造 feedback dict，委托给 run_task_stream(resume=True) 流式推进。

    推进机制
    --------
    本函数走 astream(Command(resume=feedback)) 单次推进图状态机，
    不调用 commands.py 的 resume_task（后者走 ainvoke，同步阻塞无流式推送）。
    两者不可用于同一 thread，否则会导致双重推进。

    参数
    ----
    thread_id : str
        任务线程 ID。
    user_action : str, default="continue"
        用户操作类型，透传给 feedback["user_action"]。
    modified_input : str | None
        用户修改后的输入，透传给 feedback["modified_input"]。
    retry_node_id : int | str | None
        重试节点 ID，透传给 feedback["retry_node_id"]。
    run_task_stream_fn : Callable | None
        自定义 run_task_stream 函数，用于测试注入或替换默认实现。

    返回
    ----
    str
        执行状态：
        - "paused"    → 图停在下一个 interrupt，等待用户交互
        - "complete"  → 图执行到 END
        - "error"     → 图内部抛异常
        - "cancelled" → 用户主动取消

    注意
    ----
    - 覆盖运行中任务时，cancel_task 会清理 _resume_locks 和 abort 注册，
      不会留下孤儿 worker 或锁泄漏。
    - 本函数不抛 RuntimeError；若需强制禁止覆盖，需在上层另行检查。
    """

    from task_planner.services.agent import run_task_stream as _default_run
    from task_planner.services.agent.commands import cancel_task

    existing = await state_cleaner.get(thread_id)
    if existing:
        if existing.finished:
            # 覆盖已完成的旧任务：清理 lock + unregister
            release_thread_lock(thread_id)
            await state_cleaner.unregister(thread_id)
            logger.info("[StreamMgr] 覆盖已完成的旧任务 %s", thread_id)
        else:
            # 任务仍在运行：先取消干净，再覆盖
            logger.info("[StreamMgr] 取消运行中的旧任务 %s", thread_id)
            await cancel_task(thread_id)  # 内部会 release_thread_lock + unregister_abort
            await state_cleaner.unregister(thread_id)

    # 构造 feedback ...
    feedback: dict[str, Any] = {"user_action": user_action}
    if modified_input is not None:
        feedback["modified_input"] = modified_input
    if retry_node_id is not None:
        feedback["retry_node_id"] = retry_node_id

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
