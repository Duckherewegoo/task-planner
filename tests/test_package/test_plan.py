"""test_plan.py — plan_node 消费 retrieved_examples 的单元测试。

覆盖：
- retrieved_examples 为空 / 缺失 → few_shot_text == ""
- retrieved_examples 非空 → few_shot_text 包含案例信息
- few_shot_text 正确传递给 generate_plan

本文件只测 plan_node 的编排逻辑，不测 generate_plan 内部的 prompt 拼接
（那属于 infrastructure/llm 的单元测试范围，见 test_generate_plan.py）。
"""

import pytest

from task_planner.core.graph.nodes.plan import _format_few_shot, plan_node


# ══════════════════════════════════════════════════
# _format_few_shot 纯函数测试
# ══════════════════════════════════════════════════


def test_format_few_shot_empty_list():
    """空列表返回空字符串。"""
    assert _format_few_shot([]) == ""


def test_format_few_shot_none():
    """None 输入返回空字符串（防御性）。"""
    assert _format_few_shot(None) == ""


def test_format_few_shot_single_example():
    """单条案例包含关键字段。"""
    text = _format_few_shot([
        {
            "user_input": "写周报",
            "expected_plan": {
                "task_name": "周报",
                "description": "总结本周",
                "nodes": [{"id": 1, "name": "汇总"}],
                "edges": [],
            },
        }
    ])
    assert "示例 1" in text
    assert "写周报" in text
    assert "周报" in text
    assert "汇总" in text


def test_format_few_shot_missing_fields():
    """字段缺失时降级为空，不抛异常。"""
    text = _format_few_shot([{}])
    assert "示例 1" in text  # 依然有标签，但字段为空


def test_format_few_shot_multiple_examples():
    """多条案例都有标签。"""
    text = _format_few_shot([
        {"user_input": "a", "expected_plan": {}},
        {"user_input": "b", "expected_plan": {}},
    ])
    assert "示例 1" in text
    assert "示例 2" in text


# ══════════════════════════════════════════════════
# plan_node 集成测试（mock generate_plan）
# ══════════════════════════════════════════════════


def _patch_generate_plan(monkeypatch, captured: dict):
    """公共 patch：拦 generate_plan，捕获 few_shot_text。"""

    async def _fake_generate_plan(
        user_input, intent_info, req_id, cancel_event=None, few_shot_text=""
    ):
        captured["few_shot_text"] = few_shot_text
        return {"nodes": [{"id": 1, "name": "a"}], "edges": []}

    monkeypatch.setattr(
        "task_planner.core.graph.nodes.plan.generate_plan", _fake_generate_plan
    )
    monkeypatch.setattr("task_planner.core.graph.nodes.plan.set_req_id", lambda: "rid")
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.plan.get_cancel_event", lambda: None
    )


async def test_plan_node_without_examples(monkeypatch):
    """retrieved_examples 为空时，few_shot_text == ""。"""
    captured: dict = {}
    _patch_generate_plan(monkeypatch, captured)

    state = {
        "user_input": "写个日报",
        "intent": {"category": "writing"},
        "retrieved_examples": [],
        "steps": [],
    }
    await plan_node(state, {})

    assert captured["few_shot_text"] == ""


async def test_plan_node_missing_examples_field(monkeypatch):
    """state 完全没 retrieved_examples 字段（retrieve 未挂载场景）时不崩。"""
    captured: dict = {}
    _patch_generate_plan(monkeypatch, captured)

    state = {
        "user_input": "写个日报",
        "intent": {"category": "writing"},
        # 没有 retrieved_examples 键
        "steps": [],
    }
    await plan_node(state, {})

    assert captured["few_shot_text"] == ""


async def test_plan_node_with_examples(monkeypatch):
    """retrieved_examples 非空时，few_shot_text 包含案例信息。"""
    captured: dict = {}
    _patch_generate_plan(monkeypatch, captured)

    state = {
        "user_input": "写个日报",
        "intent": {"category": "writing"},
        "retrieved_examples": [
            {
                "user_input": "写周报",
                "expected_plan": {
                    "task_name": "周报",
                    "description": "总结本周工作",
                    "nodes": [{"id": 1, "name": "汇总"}],
                    "edges": [],
                },
            }
        ],
        "steps": [],
    }
    await plan_node(state, {})

    assert "示例 1" in captured["few_shot_text"]
    assert "写周报" in captured["few_shot_text"]
    assert "周报" in captured["few_shot_text"]
