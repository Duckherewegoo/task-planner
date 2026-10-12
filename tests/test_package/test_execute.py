"""execute.py 的冒烟测试——覆盖本轮 DB 降级容错、RETRY_NODE 语义、三指针裁决。"""
from unittest.mock import AsyncMock

import pytest

from task_planner.core.graph.nodes.execute import (
    _resolve_target_index,
    execute_node,
)
from task_planner.core.graph.state import UserAction


# ── 构造最小 state ──

def _make_state(**overrides):
    base = {
        "nodes": [{"id": 1, "name": "步骤1"}, {"id": 2, "name": "步骤2"}],
        "current_node_index": 0,
        "task_id": "task-test",
        "user_input": "用户需求",
        "intent": {"category": "other"},
        "node_results": [],
        "steps": [],
        "needs_planning": True,
    }
    base.update(overrides)
    return base


def _patch_execute_common(monkeypatch, interrupt_return=None, exec_result=None):
    """统一 mock execute_node 的所有外部依赖。"""
    if exec_result is None:
        exec_result = {
            "status": "success",
            "detail": "ok",
            "retryable": False,
            "node_id": 1,
            "node_name": "步骤1",
        }
    if interrupt_return is None:
        interrupt_return = {"user_action": "continue"}

    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.mark_task_running",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.update_node_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.mark_task_failed",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.execute_single_node",
        AsyncMock(return_value=exec_result),
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.get_cancel_event",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.set_req_id",
        lambda: "rid-test",
    )
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.interrupt",
        lambda payload: interrupt_return,
    )


# ── DB 降级容错 ──

async def test_execute_survives_mark_task_running_failure(monkeypatch):
    """mark_task_running 抛异常时，execute_node 仍能完成并返回 updates。"""
    _patch_execute_common(monkeypatch)
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.mark_task_running",
        AsyncMock(side_effect=RuntimeError("DB down")),
    )

    updates = await execute_node(_make_state(), {})
    assert updates["current_node_index"] == 1
    assert updates["node_results"][0]["success"] is True


async def test_execute_survives_update_node_status_failure(monkeypatch):
    _patch_execute_common(monkeypatch)
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.update_node_status",
        AsyncMock(side_effect=RuntimeError("DB down")),
    )

    updates = await execute_node(_make_state(), {})
    assert updates["current_node_index"] == 1


async def test_execute_survives_all_db_failures(monkeypatch):
    """save.py 降级后 task_id=local-...，后续所有 DB 调用都失败——图仍不崩。"""
    _patch_execute_common(monkeypatch)
    db_fail = AsyncMock(side_effect=RuntimeError("DB down"))
    monkeypatch.setattr("task_planner.core.graph.nodes.execute.mark_task_running", db_fail)
    monkeypatch.setattr("task_planner.core.graph.nodes.execute.update_node_status", db_fail)
    monkeypatch.setattr("task_planner.core.graph.nodes.execute.mark_task_failed", db_fail)

    updates = await execute_node(_make_state(task_id="local-abc123"), {})
    assert updates["current_node_index"] == 1
    assert "node_results" in updates


# ── RETRY_NODE 语义：不前进 idx ──

async def test_retry_node_does_not_advance_index(monkeypatch):
    _patch_execute_common(
        monkeypatch,
        interrupt_return={"user_action": "retry_node"},
    )
    updates = await execute_node(_make_state(current_node_index=1), {})
    assert updates["current_node_index"] == 1  # 不前进
    assert updates["user_action"] == UserAction.RETRY_NODE
    assert updates["retry_node_id"] is None  # 已消费


async def test_continue_advances_index(monkeypatch):
    _patch_execute_common(monkeypatch, interrupt_return={"user_action": "continue"})
    updates = await execute_node(_make_state(current_node_index=0), {})
    assert updates["current_node_index"] == 1
    assert updates["retry_node_id"] is None
    assert updates["resume_from_node_index"] is None


# ── MODIFY 重置 ──

async def test_modify_resets_state(monkeypatch):
    _patch_execute_common(
        monkeypatch,
        interrupt_return={"user_action": "modify", "modified_input": "改一下需求"},
    )
    state = _make_state(current_node_index=1, task_id="old-task")
    updates = await execute_node(state, {})

    assert updates["current_node_index"] == 0
    assert updates["needs_planning"] is True       # 与 create_initial_state 对齐
    assert updates["nodes"] == []
    assert updates["task_id"] == ""
    assert updates["user_action"] is None
    assert "重新规划" in updates["status_text"]


async def test_modify_without_input_falls_back_to_continue(monkeypatch):
    """MODIFY 但无 modified_input——降级为 CONTINUE，不进入矛盾态。"""
    _patch_execute_common(
        monkeypatch,
        interrupt_return={"user_action": "modify"},  # 没有 modified_input
    )
    updates = await execute_node(_make_state(), {})
    assert updates["user_action"] == UserAction.CONTINUE


# ── 取消分支 ──

async def test_cancel_branch_clears_pointers(monkeypatch):
    _patch_execute_common(monkeypatch)
    from task_planner.infrastructure.llm import LLMCancelledError
    monkeypatch.setattr(
        "task_planner.core.graph.nodes.execute.execute_single_node",
        AsyncMock(side_effect=LLMCancelledError()),
    )
    state = _make_state(retry_node_id=1, resume_from_node_index=0)
    updates = await execute_node(state, {})

    assert updates["cancel_requested"] is True
    assert updates["user_action"] is None
    assert updates["retry_node_id"] is None
    assert updates["resume_from_node_index"] is None


# ── 三指针裁决 ──

def _resolve_state(**overrides):
    base = {
        "current_node_index": 5,
        "retry_node_id": None,
        "resume_from_node_index": None,
    }
    base.update(overrides)
    return base


NODES = [{"id": i} for i in range(10)]


def test_resolve_priority_retry_over_resume():
    assert _resolve_target_index(
        _resolve_state(retry_node_id=2, resume_from_node_index=3), NODES, "rid"
    ) == 2


def test_resolve_priority_resume_over_current():
    assert _resolve_target_index(
        _resolve_state(resume_from_node_index=3), NODES, "rid"
    ) == 3


def test_resolve_current_when_no_pointers():
    assert _resolve_target_index(_resolve_state(), NODES, "rid") == 5


def test_resolve_invalid_retry_falls_back_to_current():
    assert _resolve_target_index(
        _resolve_state(retry_node_id=999), NODES, "rid"
    ) == 5


def test_resolve_retry_by_node_id_when_not_valid_index():
    """retry_node_id 越界时，尝试按 node['id'] 匹配。"""
    nodes = [{"id": 100}, {"id": 200}, {"id": 300}]
    state = _resolve_state(retry_node_id=200, current_node_index=0)
    assert _resolve_target_index(state, nodes, "rid") == 1


def test_resolve_resume_out_of_range_falls_back():
    assert _resolve_target_index(
        _resolve_state(resume_from_node_index=999), NODES, "rid"
    ) == 5
