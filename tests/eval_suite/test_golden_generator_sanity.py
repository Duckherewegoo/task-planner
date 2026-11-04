import json
from unittest.mock import AsyncMock, patch

import pytest

from task_planner.eval_suite.golden.generator import generate_dataset, generate_golden_case


def _format_plan_fixture(sample_plan: dict) -> str:
    return json.dumps(sample_plan, ensure_ascii=False)


@pytest.mark.asyncio
async def test_generator_success_first_try(sample_plan):
    valid_json = _format_plan_fixture(sample_plan)
    with patch("task_planner.eval_suite.golden.generator.async_call_llm", return_value=valid_json):
        case = await generate_golden_case(user_input="做番茄炒蛋")
        assert case is not None
        assert case.validated is True
        assert case.expected_plan["nodes"][0]["name"] == "准备食材"
        assert len(case.expected_plan["edges"]) == 1


@pytest.mark.asyncio
async def test_generator_rejects_cyclic_dag_and_retries(sample_plan):
    valid_json = _format_plan_fixture(sample_plan)
    invalid_json = '{"nodes": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "edges": [{"from": 1, "to": 2}, {"from": 2, "to": 1}]}'
    side_effects = [invalid_json, valid_json]

    # 用 AsyncMock 替换全局 asyncio.sleep，避免递归和真实等待
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with patch(
            "task_planner.eval_suite.golden.generator.async_call_llm", side_effect=side_effects
        ) as mock_llm:
            case = await generate_golden_case(user_input="生成一个图", max_retries=2)

            assert case is not None
            assert case.validated is True
            assert mock_llm.call_count == 2
            # 断言 sleep 被调用了一次，且参数是 1.0 (1.5 ** 0)
            assert mock_sleep.call_count == 1
            assert mock_sleep.call_args[0][0] == 1.0


@pytest.mark.asyncio
async def test_generator_handles_malformed_json(sample_plan):
    valid_json = _format_plan_fixture(sample_plan)
    side_effects = ["这不是一个 JSON", valid_json]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with patch(
            "task_planner.eval_suite.golden.generator.async_call_llm", side_effect=side_effects
        ):
            case = await generate_golden_case(user_input="测试乱码", max_retries=2)
            assert case is not None
            assert case.validated is True


@pytest.mark.asyncio
async def test_generator_handles_llm_exception(sample_plan):
    valid_json = _format_plan_fixture(sample_plan)
    side_effects = [Exception("Rate Limit Exceeded"), valid_json]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with patch(
            "task_planner.eval_suite.golden.generator.async_call_llm", side_effect=side_effects
        ):
            case = await generate_golden_case(user_input="测试异常", max_retries=2)
            assert case is not None
            assert case.validated is True


@pytest.mark.asyncio
async def test_generator_fails_after_max_retries():
    invalid_json = '{"nodes": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "edges": [{"from": 1, "to": 2}, {"from": 2, "to": 1}]}'

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with patch(
            "task_planner.eval_suite.golden.generator.async_call_llm", return_value=invalid_json
        ):
            case = await generate_golden_case(user_input="必定失败", max_retries=3)

            assert case is None
            # 重试了 3 次，最后一次不等待，所以 sleep 调用 2 次
            assert mock_sleep.call_count == 2
            # 验证退避时间序列 1.0 (1.5^0), 1.5 (1.5^1)
            calls = [call.args[0] for call in mock_sleep.call_args_list]
            assert calls == [1.0, 1.5]


@pytest.mark.asyncio
async def test_generate_dataset_writes_files(tmp_path):
    specs = [{"user_input": "成功任务 1"}, {"user_input": "失败任务"}, {"user_input": "成功任务 2"}]
    invalid_json = '{"nodes": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "edges": [{"from": 1, "to": 2}, {"from": 2, "to": 1}]}'

    async def side_effect_llm(*args, **kwargs):
        prompt = kwargs["prompt"]
        if "失败任务" in prompt:
            return invalid_json
        return '{"nodes": [{"id": 1, "name": "查天气"}, {"id": 2, "name": "画图"}], "edges": [{"from": 1, "to": 2}]}'

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with patch(
            "task_planner.eval_suite.golden.generator.async_call_llm", side_effect=side_effect_llm
        ):
            cases = await generate_dataset(specs, output_dir=tmp_path, model="test-model")

            assert len(cases) == 2
            files = list(tmp_path.glob("*.json"))
            assert len(files) == 2
            assert (tmp_path / "golden_000.json").exists()
            assert (tmp_path / "golden_002.json").exists()
            assert not (tmp_path / "golden_001.json").exists()
