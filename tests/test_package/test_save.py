"""save.py 的冒烟测试——覆盖降级路径与 error 字段契约。"""
from unittest.mock import AsyncMock

import pytest

from task_planner.core.graph.nodes.save import save_node


def _make_state():
    return {
        "user_input": "用户需求",
        "intent": {"category": "other"},
        "nodes": [{"id": 1}],
        "edges": [],
        "steps": [],
    }


async def test_save_success_writes_task_id(monkeypatch):
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.create_task_with_plan",
        AsyncMock(return_value=({"task_id": "task-abc"}, {})),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.set_req_id",
        lambda: "rid",
    )

    result = await save_node(_make_state(), {})
    assert result["task_id"] == "task-abc"
    assert "已入库" in result["steps"][-1]
    assert "error" not in result


async def test_save_fallback_on_db_failure(monkeypatch):
    """DB 失败时降级为 local- 前缀 ID，图继续。"""
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.create_task_with_plan",
        AsyncMock(side_effect=RuntimeError("DB down")),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.set_req_id",
        lambda: "rid",
    )

    result = await save_node(_make_state(), {})
    assert result["task_id"].startswith("local-")
    assert "未入库" in result["steps"][-1]


async def test_save_fallback_does_not_include_error_key(monkeypatch):
    """
    关键契约：降级返回的 dict 里不应包含 "error" 键。
    原代码的 "error": "" 有两层问题——空操作 + 会清除上游已设置的 error。
    """
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.create_task_with_plan",
        AsyncMock(side_effect=RuntimeError("DB down")),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.set_req_id",
        lambda: "rid",
    )

    result = await save_node(_make_state(), {})
    assert "error" not in result


async def test_save_fallback_id_has_unique_prefix(monkeypatch):
    """两次降级产生的 local- ID 应不同。"""
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.create_task_with_plan",
        AsyncMock(side_effect=RuntimeError("DB down")),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.save.set_req_id",
        lambda: "rid",
    )

    r1 = await save_node(_make_state(), {})
    r2 = await save_node(_make_state(), {})
    assert r1["task_id"] != r2["task_id"]
    assert r1["task_id"].startswith("local-")
