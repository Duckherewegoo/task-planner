"""refine.py 的冒烟测试——重点验证 Semaphore 跨 loop 与配置解析健壮化。"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from task_planner.core.graph.nodes.refine import (
    _parse_concurrency,
    refine_node_fn,
)


# ── Semaphore 跨 loop 回归（本轮修复的关键 bug）──

def test_semaphore_does_not_bind_across_event_loops(monkeypatch):
    """
    回归测试：模块级 Semaphore 会在首次 await 时绑定 loop，
    第二次从新 loop 使用会抛 "is bound to a different event loop"。

    本测试在两次独立的 asyncio.run() 里调用 refine_node_fn——
    如果 Semaphore 是模块级，第二次会崩；修复后（函数内创建）通过。
    """
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.refine_node",
        AsyncMock(return_value={"name": "n", "details": "d", "meta": {}}),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.set_req_id",
        lambda: "rid",
    )

    state = {
        "nodes": [{"id": 1, "name": "n1"}],
        "user_input": "u",
        "intent": {"category": "other"},
        "steps": [],
    }
    config = {}

    # 第一次：新 loop
    asyncio.run(refine_node_fn(state, config))
    # 第二次：又一个新 loop——模块级 Semaphore 会在此崩
    asyncio.run(refine_node_fn(state, config))


# ── 配置解析健壮化 ──

def test_parse_concurrency_valid(monkeypatch):
    monkeypatch.setenv("REFINE_CONCURRENCY", "5")
    assert _parse_concurrency() == 5


def test_parse_concurrency_invalid_string_falls_back(monkeypatch):
    monkeypatch.setenv("REFINE_CONCURRENCY", "abc")
    assert _parse_concurrency() == 10


def test_parse_concurrency_negative_falls_back(monkeypatch):
    monkeypatch.setenv("REFINE_CONCURRENCY", "-1")
    assert _parse_concurrency() == 10


def test_parse_concurrency_zero_falls_back(monkeypatch):
    """0 会让 Semaphore(0) 永久阻塞——必须回退。"""
    monkeypatch.setenv("REFINE_CONCURRENCY", "0")
    assert _parse_concurrency() == 10


def test_parse_concurrency_missing_uses_default(monkeypatch):
    monkeypatch.delenv("REFINE_CONCURRENCY", raising=False)
    assert _parse_concurrency() == 10


# ── 基本行为 ──

async def test_refine_returns_independent_copies(monkeypatch):
    """nodes 与 refined_nodes 应是独立 list，修改一个不影响另一个。"""
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.refine_node",
        AsyncMock(return_value={"name": "refined", "details": "d", "meta": {}}),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.set_req_id",
        lambda: "rid",
    )

    state = {
        "nodes": [{"id": 1, "name": "n1"}],
        "user_input": "u",
        "intent": {"category": "other"},
        "steps": [],
    }
    result = await refine_node_fn(state, {})

    assert result["refined_nodes"] is not result["nodes"]
    assert result["refined_nodes"] == result["nodes"]


async def test_refine_single_node_failure_does_not_block_others(monkeypatch):
    """单节点失败降级为 fallback，不影响其他节点。"""
    async def _fake_refine(n, *a, **kw):
        if n.get("id") == 1:
            raise RuntimeError("节点1失败")
        return {"name": "ok", "details": "d", "meta": {}}

    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.refine_node",
        _fake_refine,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.set_req_id",
        lambda: "rid",
    )

    state = {
        "nodes": [{"id": 1, "name": "n1"}, {"id": 2, "name": "n2"}],
        "user_input": "u",
        "intent": {"category": "other"},
        "steps": [],
    }
    result = await refine_node_fn(state, {})
    assert len(result["refined_nodes"]) == 2
    assert "失败" in result["refined_nodes"][0]["details"]
    assert result["refined_nodes"][1]["name"] == "ok"


async def test_refine_cancel_marks_unsubmitted_nodes(monkeypatch):
    """cancel_event 设置后，未提交的节点应被标记为'未提交'。"""
    cancel_event = asyncio.Event()

    async def _fake_refine(n, *a, **kw):
        # 第一个节点执行时设置取消
        cancel_event.set()
        return {"name": "n", "details": "d", "meta": {}}

    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.refine_node",
        _fake_refine,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda cfg: cancel_event,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.set_req_id",
        lambda: "rid",
    )

    state = {
        "nodes": [{"id": i} for i in range(5)],
        "user_input": "u",
        "intent": {"category": "other"},
        "steps": [],
    }
    result = await refine_node_fn(state, {})

    assert len(result["refined_nodes"]) == 5
    # 后面的节点应带"未提交"标记
    assert any("未提交" in n["details"] for n in result["refined_nodes"][1:])
