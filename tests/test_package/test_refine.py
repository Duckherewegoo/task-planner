"""refine.py 的冒烟测试——重点验证 Semaphore 跨 loop 与配置解析健壮化。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from task_planner.core.graph.nodes.refine import (
    _parse_concurrency,
    refine_node,
)

# ── Semaphore 跨 loop 回归（本轮修复的关键 bug）──


def test_semaphore_does_not_bind_across_event_loops(monkeypatch):
    """
    回归测试：模块级 Semaphore 会在首次 await 时绑定 loop，
    第二次从新 loop 使用会抛 "is bound to a different event loop"。

    本测试在两次独立的 asyncio.run() 里调用 refine_node——
    如果 Semaphore 是模块级，第二次会崩；修复后（函数内创建）通过。
    """
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.refine_node_llm",
        AsyncMock(return_value={"name": "n", "details": "d", "meta": {}}),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda: None,
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
    asyncio.run(refine_node(state, config))
    # 第二次：又一个新 loop——模块级 Semaphore 会在此崩
    asyncio.run(refine_node(state, config))


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
        "task_planner.core.graph.nodes.refine.refine_node_llm",
        AsyncMock(return_value={"name": "refined", "details": "d", "meta": {}}),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda: None,
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
    result = await refine_node(state, {})

    assert result["refined_nodes"] is not result["nodes"]
    assert result["refined_nodes"] == result["nodes"]


async def test_refine_single_node_failure_does_not_block_others(monkeypatch):
    """单节点失败降级为 fallback，不影响其他节点。"""

    async def _fake_refine(n, *a, **kw):
        if n.get("id") == 1:
            raise RuntimeError("节点1失败")
        return {"name": "ok", "details": "d", "meta": {}}

    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.refine_node_llm",
        _fake_refine,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda: None,
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
    result = await refine_node(state, {})
    assert len(result["refined_nodes"]) == 2
    assert "失败" in result["refined_nodes"][0]["details"]
    assert result["refined_nodes"][1]["name"] == "ok"


async def test_refine_cancel_mid_loop_marks_remaining_unsubmitted(monkeypatch):
    """
    cancel_event 在主循环迭代期间被**外部**设置——
    剩余节点不提交，标记为"未提交"。

    与 test_refine_cancel_before_submit_marks_all_unsubmitted 的区别：
      - 那条测"入口即取消"——循环第一次检查就 break，0 提交
      - 这条测"迭代中途取消"——部分提交、部分未提交

    可达性依赖 refine_node 主循环里的 `await asyncio.sleep(0)`：
      没有这个让出点，主循环同步执行完毕，外部 set 无法被感知。
    """
    import asyncio

    cancel_event = asyncio.Event()

    async def _fake_refine(n, *a, **kw):
        # 模拟正常 LLM 调用——本测试的取消来自外部，不来自节点内部
        return {"name": "n", "details": "d", "meta": {}}

    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.refine_node_llm",
        _fake_refine,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.get_cancel_event",
        lambda: cancel_event,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.refine.set_req_id",
        lambda: "rid",
    )

    # 后台任务：在主循环跑了几轮之后设置 cancel——
    # 模拟 commands.cancel_task 在用户点击时的行为。
    async def _cancel_later():
        # 让主循环至少迭代 3 次，确保它进入"边提交边检查"的状态
        for _ in range(3):
            await asyncio.sleep(0)
        cancel_event.set()

    cancel_task = asyncio.create_task(_cancel_later())

    state = {
        "nodes": [{"id": i} for i in range(20)],
        "user_input": "u",
        "intent": {"category": "other"},
        "steps": [],
    }
    result = await refine_node(state, {})

    await cancel_task  # 等后台任务结束，避免 "task was destroyed" 警告

    # 核心断言：总数不变（未提交的用占位补齐）
    assert len(result["refined_nodes"]) == 20

    # 至少有一个"未提交"——证明主循环在迭代中途 break 了
    unsubmitted = [n for n in result["refined_nodes"] if "未提交" in n.get("details", "")]
    assert len(unsubmitted) > 0, (
        "预期存在未提交节点（主循环中途 break），实际全部已提交或全部未提交。"
        "可能是 sleep(0) 让出点被移除，或后台任务的 sleep 次数需要调。"
    )

    # 且应该有一部分是已提交的（前几轮迭代已完成）
    submitted = [n for n in result["refined_nodes"] if "未提交" not in n.get("details", "")]
    assert len(submitted) > 0, "预期存在已提交节点（前面几轮迭代）"
