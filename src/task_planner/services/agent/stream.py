"""stream.py — run_task_stream 核心生成器"""

import asyncio
import contextvars
import traceback
from collections.abc import AsyncGenerator
from typing import Any

from langgraph.types import Command

from task_planner.core.graph.workflow import get_graph
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.utils.context import cancel_event_var

from .session import acquire_session, pop_session
from .state import make_initial_state
from .view import extract_snapshot, is_graph_finished

logger = get_logger(__name__)


async def run_task_stream(
    user_input: str,
    thread_id: str,
    enable_refine: bool = True,
    resume: bool = False,
    resume_data: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any]]:
    """异步生成器，实时推送任务执行进度。

    两种启动模式：
      - resume=False：从 make_initial_state 构造初始状态，跑完整任务
      - resume=True ：从 checkpoint 恢复，用 Command(resume=resume_data) 推进图

    ⚠️ resume 模式必须传 resume_data：
       `astream(None, config)` 只对 interrupt_before/interrupt_after 的静态
       中断有效，对 `interrupt()` 动态中断无效——图会原地重抛 interrupt，
       不会推进。必须用 `Command(resume=feedback)` 才能真正恢复。

    ⚠️ 暂停 vs 完成的区分：
       astream 结束有两种可能：
         a) 图执行到 END     → next 为空 → 任务完成
         b) 图停在下一个 interrupt → next 非空 → 等待用户交互
       本函数通过 `graph.aget_state(config).next` 判断是哪种情况，
       据此决定是否 pop session（暂停时保留，供下次 resume）。

    Args:
        user_input:   用户输入（resume 模式下忽略）
        thread_id:    会话 ID
        enable_refine: 是否启用节点细化（resume 模式下忽略）
        resume:       是否从 checkpoint 恢复
        resume_data:  resume=True 时传给 Command(resume=...) 的 dict，
                      键名规则见 commands.py 顶部 docstring
                      （user_action / modified_input / retry_node_id）

    Yields:
        snapshot dict，包含 type / 任务进度等字段。
        最后一个事件的 type 一定是 complete / cancelled / timeout / error
        或 paused 之一。
    """
    if not isinstance(thread_id, str):
        raise TypeError(f"thread_id must be str, got {type(thread_id).__name__}: {thread_id!r}")

    if resume and resume_data is None:
        # 显式报错，避免静默走 astream(None) 的旧路径
        raise ValueError(
            "run_task_stream(resume=True) 必须提供 resume_data"
            "（Command(resume=...) 的载荷）；"
            "astream(None) 对 interrupt() 无效。"
        )

    rid = set_req_id()
    logger.info("[Agent] 启动任务 | thread=%s resume=%s req=%s", thread_id, resume, rid)

    session = await acquire_session(thread_id, resume)

    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)

    graph_finished = False

    try:
        event_count = 0
        g = await asyncio.to_thread(get_graph)

        if resume:
            logger.info("[Agent] resume 模式，用 Command(resume=...) 推进 | thread=%s", thread_id)
            stream_iter = g.astream(
                Command(resume=resume_data),
                session.config,
                stream_mode=["values", "updates"],
            )
        else:
            initial_state = make_initial_state(user_input, thread_id)
            logger.info("[Agent] initial_state keys: %s", list(initial_state.keys()))
            stream_iter = g.astream(
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

            snapshot = extract_snapshot(data, session, mode=mode)
            if snapshot is not None:
                yield snapshot

            if is_graph_finished(data, mode):
                graph_finished = True
                break

        logger.info("[Agent] graph.astream() 结束，共 %d 个事件", event_count)

        # astream 结束后，区分"图暂停" vs "图完成"
        if not graph_finished:
            final_snapshot = await g.aget_state(session.config)
            if final_snapshot and not final_snapshot.next:
                # next 为空 → 图执行到 END
                graph_finished = True
            elif final_snapshot and final_snapshot.next:
                # next 非空 → 图停在下一个 interrupt，等待用户交互
                logger.info(
                    "[Agent] 图暂停在 %s，等待用户交互 | thread=%s",
                    final_snapshot.next,
                    thread_id,
                )
                yield {
                    "type": "paused",
                    "task_id": thread_id,
                    "next_nodes": list(final_snapshot.next),
                }

    except asyncio.CancelledError:
        session.cancel_event.set()
        logger.warning("[Agent] 任务被外部取消 (req=%s)", rid)
        yield {"type": "cancelled", "task_id": thread_id}
        graph_finished = True
        raise

    except Exception as exc:
        logger.error("[Agent] 异常: %s\n%s", exc, traceback.format_exc())
        yield {
            "type": "error",
            "error": str(exc),
            "task_id": thread_id,
        }
        graph_finished = True

    finally:
        session.cancel_event.set()
        # 只在图真正完成时才 pop session；暂停时保留，供下次 resume 复用
        if graph_finished:
            await pop_session(thread_id)
            logger.info("[Agent] session 清理完成 | thread=%s req=%s", thread_id, rid)
        else:
            logger.info("[Agent] 图暂停，保留 session | thread=%s req=%s", thread_id, rid)


__all__ = ["run_task_stream"]
