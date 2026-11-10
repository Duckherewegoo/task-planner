"""test_generate_plan.py — generate_plan 内部 few-shot 注入测试。

覆盖：
- few_shot_text 非空 → prompt 里出现 few-shot 段落
- few_shot_text 为空 → prompt 不变（与改动前逐字节一致）
- Mock 模式 → few-shot 被忽略，不调用 LLM

patch 路径说明：
  generate_plan 定义在 task_planner.infrastructure.llm.api 里，
  它通过 `from .core import async_call_llm` 导入，因此引用落在
  api 模块命名空间。patch 必须打 `task_planner.infrastructure.llm.api.async_call_llm`，
  而不是 `.core.async_call_llm`。
"""

import pytest

from task_planner.infrastructure.llm import generate_plan

# ══════════════════════════════════════════════════
# 辅助：构造可控的 hub + LLM 返回
# ══════════════════════════════════════════════════


class _FakeDev:
    """替换 _hub.dev 的最小实现，让 generate_plan 走真实 LLM 分支。"""

    USE_MOCK_LLM = False
    LLM_PLANNER_MODEL = "fake-model"
    LLM_PLANNER_ENABLE_THINKING = False


class _FakeHub:
    dev = _FakeDev()


_VALID_PLAN_JSON = '{"task_name":"t","description":"","nodes":[{"id":1,"name":"a"}],"edges":[]}'


def _patch_llm(monkeypatch, captured: dict, response: str = _VALID_PLAN_JSON):
    """公共 patch：拦 async_call_llm 捕获 prompt，让 _hub 走真实分支。"""

    async def _fake_call_llm(*, model, prompt, enable_thinking, req_id, cancel_event=None):
        captured["prompt"] = prompt
        captured["model"] = model
        return response

    monkeypatch.setattr("task_planner.infrastructure.llm.api.async_call_llm", _fake_call_llm)
    monkeypatch.setattr("task_planner.infrastructure.llm.api._hub", _FakeHub())


# ══════════════════════════════════════════════════
# few-shot 注入测试
# ══════════════════════════════════════════════════


async def test_generate_plan_without_few_shot(monkeypatch):
    """few_shot_text 为空时，prompt 里不含 few-shot 段落。"""
    captured: dict = {}
    _patch_llm(monkeypatch, captured)

    plan = await generate_plan(
        user_input="写日报",
        intent_info={"category": "writing", "summary": ""},
        req_id="rid",
        # few_shot_text 默认空
    )

    assert plan["task_name"] == "t"
    assert "相似历史案例" not in captured["prompt"]
    assert "few-shot" not in captured["prompt"].lower()


async def test_generate_plan_with_few_shot(monkeypatch):
    """few_shot_text 非空时，prompt 追加 few-shot 段落。"""
    captured: dict = {}
    _patch_llm(monkeypatch, captured)

    await generate_plan(
        user_input="写日报",
        intent_info={"category": "writing", "summary": ""},
        req_id="rid",
        few_shot_text="### 示例 1\n用户需求：写周报\n参考计划：\n- 任务名：周报",
    )

    prompt = captured["prompt"]
    assert "相似历史案例" in prompt
    assert "写周报" in prompt
    assert "周报" in prompt


async def test_generate_plan_empty_few_shot_treated_as_absent(monkeypatch):
    """few_shot_text="" 与不传等价（幂等性）。"""
    captured_with = {}
    captured_without = {}

    _patch_llm(monkeypatch, captured_with)
    await generate_plan("写日报", {"category": "writing"}, "rid", few_shot_text="")

    _patch_llm(monkeypatch, captured_without)
    await generate_plan("写日报", {"category": "writing"}, "rid")

    # 两次 prompt 应完全一致
    assert captured_with["prompt"] == captured_without["prompt"]


async def test_generate_plan_mock_mode_ignores_few_shot(monkeypatch):
    """Mock 模式不走 prompt 拼接路径，few-shot 被忽略，不调用 LLM。"""
    called = {"count": 0}

    async def _should_not_be_called(**kwargs):
        called["count"] += 1
        return _VALID_PLAN_JSON

    monkeypatch.setattr("task_planner.infrastructure.llm.api.async_call_llm", _should_not_be_called)

    class _MockDev:
        USE_MOCK_LLM = True

    class _MockHub:
        dev = _MockDev()

    monkeypatch.setattr("task_planner.infrastructure.llm.api._hub", _MockHub())

    plan = await generate_plan(
        "写日报",
        {"category": "writing"},
        "rid",
        few_shot_text="### 示例 1\n用户需求：写周报",
    )

    # mock_plan 被返回，LLM 未被调用
    assert called["count"] == 0
    assert "nodes" in plan


# ══════════════════════════════════════════════════
# 异常分支的 few-shot 隔离性
# ══════════════════════════════════════════════════


async def test_generate_plan_few_shot_does_not_mask_llm_error(monkeypatch):
    """LLM 抛异常时，few-shot 存在与否不影响错误降级路径。"""
    from task_planner.infrastructure.llm.errors import LLMClientError

    async def _fail(**kwargs):
        raise LLMClientError("simulated client error", "rid")

    monkeypatch.setattr("task_planner.infrastructure.llm.api.async_call_llm", _fail)
    monkeypatch.setattr("task_planner.infrastructure.llm.api._hub", _FakeHub())

    plan = await generate_plan(
        "写日报",
        {"category": "writing"},
        "rid",
        few_shot_text="### 示例 1\n用户需求：写周报",
    )

    # 走降级路径，返回规划失败占位
    assert plan["task_name"] == "规划失败"
    assert plan["nodes"] == []
    assert plan["edges"] == []
