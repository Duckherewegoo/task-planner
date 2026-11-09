"""三指针裁决（retry > resume > current）的单元测试。

覆盖 state.py TaskState docstring 声明的优先级契约，以及
execute.py::_resolve_target_index 的两个历史 bug 回归：
  1. 字符串数字指针（"3"）不应被静默降级为 current。
  2. node["id"] 的 int/str 类型不一致不应导致 id 匹配失败。
"""

from __future__ import annotations

import pytest

from task_planner.core.graph.nodes.execute import _resolve_target_index
from task_planner.core.graph.state import create_initial_state


def _state(**kwargs) -> dict:
    return create_initial_state("user input", "thread-1", **kwargs)


NODES_1_2_3 = [
    {"id": 1, "name": "步骤1"},
    {"id": 2, "name": "步骤2"},
    {"id": 3, "name": "步骤3"},
]


# ── 基础优先级 ───────────────────────────────────────────────

def test_defaults_to_current_when_no_pointer():
    state = _state(current_node_index=1)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


def test_retry_wins_over_resume_and_current():
    state = _state(
        current_node_index=0,
        retry_node_index=2,
        resume_from_node_index=1,
    )
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 2


def test_resume_wins_over_current():
    state = _state(
        current_node_index=0,
        retry_node_index=None,
        resume_from_node_index=2,
    )
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 2


def test_retry_float_pointer_falls_back_to_current():
    """float(3.0) 不应作为合法指针——nodes[3.0] 会抛 TypeError。"""
    state = _state(current_node_index=0, retry_node_index=3.0)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0


def test_resume_float_pointer_falls_back_to_current():
    state = _state(current_node_index=0, resume_from_node_index=1.0)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0

# ── retry_node_index：index 语义 ───────────────────────────────

@pytest.mark.parametrize("retry_idx", [0, 1, 2])
def test_retry_valid_index(retry_idx):
    state = _state(retry_node_index=retry_idx)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == retry_idx


# ── Bug 1 回归：字符串数字必须归一化 ──────────────────────────

@pytest.mark.parametrize("retry_str", ["0", "1", "2"])
def test_retry_string_number_is_normalized(retry_str):
    """workflow.py 从 URL/JSON 传入的指针可能为 str，必须归一化为 int。"""
    state = _state(retry_node_index=retry_str)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == int(retry_str)


def test_resume_string_number_is_normalized():
    state = _state(resume_from_node_index="2")
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 2


# ── Bug 2 回归：node["id"] int/str 双兼容 ─────────────────────

def test_retry_out_of_range_matches_node_id_int():
    """retry=100 超出 index 范围，但 nodes[2]["id"] == 100，应匹配 index=2。"""
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
        {"id": 100, "name": "c"},
    ]
    state = _state(retry_node_index=100)
    assert _resolve_target_index(state, nodes, "req") == 2


def test_retry_out_of_range_matches_node_id_str():
    """node["id"] 是 str（LLM 漂移），用户传 int，也必须能匹配。"""
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
        {"id": "100", "name": "c"},   # ← 漂移成 str
    ]
    state = _state(retry_node_index=100)   # ← 用户传 int
    assert _resolve_target_index(state, nodes, "req") == 2


def test_retry_str_matches_int_node_id():
    """对称方向：用户传 "100"，node["id"] 是 int 100，也必须匹配。"""
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
        {"id": 100, "name": "c"},
    ]
    state = _state(retry_node_index="100")
    assert _resolve_target_index(state, nodes, "req") == 2


# ── 降级路径 ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad_retry", [True, False, "not-a-number", -1, 999])
def test_retry_invalid_pointer_falls_back_to_current(bad_retry):
    state = _state(current_node_index=1, retry_node_index=bad_retry)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


def test_resume_out_of_range_falls_back_to_current():
    state = _state(current_node_index=0, resume_from_node_index=999)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0


def test_resume_bool_falls_back_to_current():
    """bool 是 int 子类，必须特判避免 True 被当成 index=1。"""
    state = _state(current_node_index=0, resume_from_node_index=True)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0


# ── index vs id 双语义的设计决策（文档化行为）────────────────

def test_retry_index_wins_over_id_when_both_valid():
    """设计决策：retry_node_index 优先按 index 解释。
    若 nodes[1]["id"] == 100 且用户传 retry=1，返回 index=1 而非 id=100 所在位置。
    这是与 workflow.py 命名一致的显式决策，不是 bug。
    """
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 100, "name": "b"},    # ← index=1，id=100
        {"id": 3, "name": "c"},
    ]
    state = _state(retry_node_index=1)
    assert _resolve_target_index(state, nodes, "req") == 1


# ── 空 nodes 防御 ─────────────────────────────────────────────

def test_empty_nodes_returns_zero():
    state = _state()
    assert _resolve_target_index(state, [], "req") == 0
