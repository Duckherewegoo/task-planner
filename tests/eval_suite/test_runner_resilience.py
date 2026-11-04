import asyncio
import pytest
from task_planner.eval_suite.runners.agent_runner import run_agent

@pytest.mark.asyncio
async def test_runner_timeout():
    """测试超时硬截断：假装 Agent 卡死，2秒不返回。"""
    async def mock_hang_run_fn(*args, **kwargs):
        await asyncio.sleep(10)
        yield {} # 永远不会执行到这里

    trace = await run_agent(
        case_id="t-timeout",
        user_input="卡死测试",
        timeout=2.0,
        run_fn=mock_hang_run_fn
    )
    assert trace.error is not None
    assert "超时" in trace.error
    assert trace.duration_sec >= 2.0

@pytest.mark.asyncio
async def test_runner_cancellation():
    """测试取消事件（防污染/恢复力）：模拟外部触发 cancel。"""
    async def mock_cancel_run_fn(*args, **kwargs):
        raise asyncio.CancelledError("用户中断")
        yield {}

    trace = await run_agent(
        case_id="t-cancel",
        user_input="取消测试",
        run_fn=mock_cancel_run_fn
    )
    # 如果你的 except 没修，这里会直接抛 CancelledError 导致测试挂掉
    assert trace.error is not None
    assert "取消" in trace.error or "Cancelled" in trace.error

@pytest.mark.asyncio
async def test_runner_catches_business_error():
    """测试业务异常：LLM 挂了或者 DB 断了，应该记录错误而不是崩溃。"""
    async def mock_fail_run_fn(*args, **kwargs):
        raise ValueError("LLM API Rate Limit Exceeded")
        yield {}

    trace = await run_agent(
        case_id="t-error",
        user_input="报错测试",
        run_fn=mock_fail_run_fn
    )
    assert trace.error == "LLM API Rate Limit Exceeded"
