"""路由函数的冒烟测试——覆盖本轮归一化与拒绝逻辑的改动。"""
import pytest

from task_planner.core.graph.routes import (
    route_after_execute,
    route_after_intent,
    route_after_render,
)
from task_planner.core.graph.state import UserAction


# ── route_after_intent ──

def test_route_after_intent_cancel_priority():
    """cancel_requested 优先于一切。"""
    assert route_after_intent({"cancel_requested": True, "needs_planning": True}) == "cancel"


def test_route_after_intent_direct_answer_when_no_planning():
    assert route_after_intent({"cancel_requested": False, "needs_planning": False}) == "direct_answer"


def test_route_after_intent_plan_default():
    assert route_after_intent({"cancel_requested": False, "needs_planning": True}) == "plan"


def test_route_after_intent_needs_planning_missing_defaults_true():
    """needs_planning 缺失时 fallback 是 True（与 create_initial_state 对齐）。"""
    assert route_after_intent({"cancel_requested": False}) == "plan"


# ── route_after_execute ──

def _exec_state(**overrides):
    """构造最小 exec state，未指定字段用合理默认值。"""
    base = {
        "cancel_requested": False,
        "user_action": None,
        "current_node_index": 0,
        "nodes": [{"id": 1}, {"id": 2}],
    }
    base.update(overrides)
    return base


def test_route_after_execute_cancel_requested_priority():
    """cancel_requested=True 优先于 user_action。"""
    assert route_after_execute(_exec_state(cancel_requested=True, user_action=UserAction.CONTINUE)) == "cancel"


def test_route_after_execute_user_action_cancel():
    assert route_after_execute(_exec_state(user_action=UserAction.CANCEL)) == "cancel"


def test_route_after_execute_user_action_modify():
    assert route_after_execute(_exec_state(user_action=UserAction.MODIFY)) == "intent"


def test_route_after_execute_user_action_retry_node():
    assert route_after_execute(_exec_state(user_action=UserAction.RETRY_NODE)) == "execute"


def test_route_after_execute_accepts_valid_checkpointer_string():
    """checkpointer 反序列化后 UserAction 可能变成字符串——应被归一化。"""
    assert route_after_execute(_exec_state(user_action="cancel")) == "cancel"
    assert route_after_execute(_exec_state(user_action="modify")) == "intent"
    assert route_after_execute(_exec_state(user_action="retry_node")) == "execute"


def test_route_after_execute_rejects_invalid_string():
    """非法字符串应抛 ValueError，不静默走错分支。"""
    with pytest.raises(ValueError):
        route_after_execute(_exec_state(user_action="foobar"))


def test_route_after_execute_rejects_non_string_non_enum():
    """非 str 非 UserAction 的值也应被拒绝（由 validate_user_action 抛出）。"""
    with pytest.raises((ValueError, TypeError)):
        route_after_execute(_exec_state(user_action=12345))


def test_route_after_execute_no_action_not_finished():
    """无 user_action 且 idx 未越界 → execute。"""
    assert route_after_execute(_exec_state(current_node_index=0, nodes=[{}, {}])) == "execute"


def test_route_after_execute_no_action_finished():
    """无 user_action 且 idx 越界 → finish。"""
    assert route_after_execute(_exec_state(current_node_index=5, nodes=[{}, {}])) == "finish"


# ── route_after_render ──

def test_route_after_render_always_execute():
    assert route_after_render({}) == "execute"
