"""
agent_runner.py — 运行 Agent 并收集轨迹。

关键设计：
    - run_fn 参数化：默认用 run_task_stream，测试时可注入 Mock
    - timeout 真正生效：用 asyncio.wait_for 包裹
    - 从 snapshot 的正确字段提取数据（不猜字段名）
"""

import asyncio
import time
from collections.abc import Callable

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

from ..types import AgentTrace

logger = get_logger("task_planner.eval_suite.runner")

# 默认的 Agent 执行函数（延迟导入 + 缓存）
_RUN_FN_CACHE: Callable | None = None


def _default_run_fn() -> Callable:
    global _RUN_FN_CACHE
    if _RUN_FN_CACHE is None:
        from task_planner.services.agent import run_task_stream

        _RUN_FN_CACHE = run_task_stream
    return _RUN_FN_CACHE


async def run_agent(
    case_id: str,
    user_input: str,
    *,
    use_mock: bool | None = None,
    timeout: float = 120.0,
    run_fn: Callable | None = None,
) -> AgentTrace:
    """
    运行 Agent，收集完整轨迹。

    Args:
        use_mock: None=跟随配置；True=强制 Mock；False=强制真实 LLM
        timeout:  硬超时（asyncio.wait_for 实现）
        run_fn:   注入的执行函数，用于测试替身
    """
    original = None
    if use_mock is not None:
        original = _hub.dev.USE_MOCK_LLM
        _hub.dev.USE_MOCK_LLM = use_mock

    thread_id = f"eval_{case_id}_{int(time.time())}"
    trace = AgentTrace(
        case_id=case_id,
        user_input=user_input,
        thread_id=thread_id,
        mock_mode=_hub.dev.USE_MOCK_LLM,
    )

    if run_fn is None:
        run_fn = _default_run_fn()
    t0 = time.perf_counter()

    async def _consume() -> None:
        async for event in run_fn(user_input, thread_id):
            # 从 snapshot 提取正确字段
            if event.get("intent"):
                trace.intent = event["intent"]
            if event.get("nodes"):
                trace.plan = {
                    "nodes": event["nodes"],
                    "edges": event.get("edges", []),
                }
            if event.get("node_states") is not None:
                trace.node_states = event["node_states"]
            if event.get("steps"):
                trace.steps = event["steps"]
            if event.get("error"):
                trace.error = event["error"]
                break

    try:
        await asyncio.wait_for(_consume(), timeout=timeout)
    except TimeoutError:
        trace.error = f"超时 ({timeout}s)"
        logger.warning("[Runner] %s 超时", case_id)
    except Exception as e:
        trace.error = str(e)
        logger.exception("[Runner] %s 执行异常", case_id)
    finally:
        trace.duration_sec = round(time.perf_counter() - t0, 2)
        if original is not None:
            _hub.dev.USE_MOCK_LLM = original

    return trace
