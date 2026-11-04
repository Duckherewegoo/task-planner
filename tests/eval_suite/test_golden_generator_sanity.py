import asyncio
import json
import pytest
from unittest.mock import patch

from task_planner.eval_suite.golden.generator import generate_golden_case
from task_planner.eval_suite.types import Difficulty

# 把 conftest 里的 fixture 拿过来用
def _format_plan_fixture(sample_plan: dict) -> str:
    """将 fixture 转为 generator 期望的 JSON 字符串"""
    return json.dumps(sample_plan, ensure_ascii=False)


@pytest.mark.asyncio
async def test_generator_success_first_try(sample_plan, sample_intent_plan):
    """测试完美情况：第一次就返回合法 JSON，且验证通过。"""
    valid_json = _format_plan_fixture(sample_plan)
    
    with patch("task_planner.eval_suite.golden.generator.async_call_llm", return_value=valid_json):
        case = await generate_golden_case(user_input="做番茄炒蛋")
        
        assert case is not None
        assert case.validated is True
        # 此时验证的是 conftest 里的样例
        assert case.expected_plan["nodes"][0]["name"] == "准备食材"
        assert len(case.expected_plan["edges"]) == 1


@pytest.mark.asyncio
async def test_generator_handles_llm_exception(mock_sleep, sample_plan):
    """恢复力：LLM 抛出网络异常，应该被捕获并重试。"""
    valid_json = _format_plan_fixture(sample_plan)
    side_effects = [Exception("Rate Limit Exceeded"), valid_json]
    
    with patch("task_planner.eval_suite.golden.generator.async_call_llm", side_effect=side_effects):
        case = await generate_golden_case(user_input="测试异常", max_retries=2)
        assert case is not None
        assert case.validated is True
