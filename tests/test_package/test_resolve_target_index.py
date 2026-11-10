"""三指针裁决（retry > resume > current）的单元测试。

本文件锁定 execute.py::_resolve_target_index 的核心契约。自方案 B 起，
两个指针的语义被彻底拆分，不允许隐式互转：

  - retry_node_id        只按 node["id"] 匹配（int/str 双向兼容）
  - resume_from_node_index 只按索引匹配（0..len(nodes)-1）
  - current_node_index   两者皆空时的自然续传位

设计动机：早期实现让 retry_node_index 兼容 index 与 id 双语义，
在"节点 id 与索引错位"的场景下会静默执行错误的节点。方案 B 通过
命名区分（id 与 index）与语义隔离，杜绝了这一类静默错误。

本测试同时回归以下历史缺陷：
  1. 字符串数字指针（"2"）被 isinstance(x, int) 漏判而静默降级。
  2. node["id"] 为 int、用户传 str（或反之）时严格相等匹配失败。
  3. bool 作为 int 子类混入指针，导致 True 被当成 index=1。
  4. float 指针穿透到列表下标，触发运行时 TypeError。

优先级契约的权威声明见 state.py::TaskState 的 docstring；本文件
中的测试断言必须与该契约保持一致。若契约发生变更，两处需同步更新。
"""

from __future__ import annotations

import pytest

from task_planner.core.graph.nodes.execute import _resolve_target_index
from task_planner.core.graph.state import create_initial_state

# ══════════════════════════════════════════════════
# 辅助构造
# ══════════════════════════════════════════════════


def _state(**kwargs) -> dict:
    """构造一个初始态，按需覆盖字段。"""
    return create_initial_state("user input", "thread-1", **kwargs)


# 基线场景：id 与 index 一一对应（id=1 在 index=0，id=2 在 index=1...）
NODES_1_2_3 = [
    {"id": 1, "name": "步骤1"},
    {"id": 2, "name": "步骤2"},
    {"id": 3, "name": "步骤3"},
]


# ══════════════════════════════════════════════════
# 优先级契约：retry > resume > current
# ══════════════════════════════════════════════════


def test_defaults_to_current_when_no_pointer():
    """两指针皆空时，按 current_node_index 自然续传。"""
    state = _state(current_node_index=1)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


def test_retry_id_wins_over_resume_and_current():
    """三指针同时存在时，retry_node_id 优先级最高。"""
    state = _state(
        current_node_index=0,
        retry_node_id=2,
        resume_from_node_index=2,
    )
    # retry_node_id=2 命中 index=1 的节点；即使 resume=2 存在也不被采用。
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


def test_resume_wins_over_current():
    """retry_node_id 为空时，resume_from_node_index 覆盖 current_node_index。"""
    state = _state(
        current_node_index=0,
        resume_from_node_index=2,
    )
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 2


# ══════════════════════════════════════════════════
# retry_node_id：只走 ID 语义
# ══════════════════════════════════════════════════


def test_retry_id_lookup_when_id_equals_index_plus_one():
    """基线场景：id=2 位于 index=1。

    这是最基础的按 id 匹配验证，确保返回值是基于节点在列表中的
    实际位置，而不是 id 的数值本身。
    """
    state = _state(retry_node_id=2)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


def test_retry_id_when_id_differs_from_index():
    """节点 id 与索引错位时，必须按 id 命中，不能按索引命中。

    场景：nodes[1]["id"] == 100。用户传 retry_node_id=100，
    期望返回 index=1。若错误地按 index 语义解释 100，会越界或
    命中错误的节点。
    """
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 100, "name": "b"},
        {"id": 3, "name": "c"},
    ]
    state = _state(retry_node_id=100)
    assert _resolve_target_index(state, nodes, "req") == 1


def test_retry_id_when_id_matches_lower_index_value():
    """警惕静默错误：id=1 与某个越界数值不冲突时，仍按 id 命中。

    场景：nodes 只有 2 个元素，但 nodes[1]["id"] == 500。用户传
    retry_node_id=1，应命中 nodes[0]（id=1），而不是把 1 当作 index。
    """
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 500, "name": "b"},
    ]
    state = _state(retry_node_id=1)
    assert _resolve_target_index(state, nodes, "req") == 0


# ══════════════════════════════════════════════════
# retry_node_id：int/str 双向匹配
# ══════════════════════════════════════════════════


def test_retry_id_int_matches_str_node_id():
    """node["id"] 被 LLM 漂移为 str 时，用户传 int 仍须命中。"""
    nodes = [
        {"id": 1, "name": "a"},
        {"id": "100", "name": "b"},
        {"id": 3, "name": "c"},
    ]
    state = _state(retry_node_id=100)
    assert _resolve_target_index(state, nodes, "req") == 1


def test_retry_id_str_matches_int_node_id():
    """对称方向：用户传 "100"，node["id"] 为 int 100，仍须命中。"""
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 100, "name": "b"},
        {"id": 3, "name": "c"},
    ]
    state = _state(retry_node_id="100")
    assert _resolve_target_index(state, nodes, "req") == 1


def test_retry_id_string_numeric_is_coerced_then_matched():
    """字符串数字先归一化为 int，再按 id 匹配。

    这是历史缺陷回归：早期版本用 isinstance(x, int) 判断，导致
    "2" 被漏判为非法指针而静默降级为 current。
    """
    state = _state(retry_node_id="2")
    # "2" -> 2 -> 命中 nodes[1]（id=2）。
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


# ══════════════════════════════════════════════════
# resume_from_node_index：只走 index 语义
# ══════════════════════════════════════════════════


@pytest.mark.parametrize("resume_idx", [0, 1, 2])
def test_resume_valid_index(resume_idx):
    """合法索引直接返回该索引，不做任何 id 匹配。"""
    state = _state(resume_from_node_index=resume_idx)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == resume_idx


def test_resume_ignores_node_id_value():
    """resume 只按 index 解释，不因节点 id 数值而偏移。

    场景：nodes[1]["id"] == 100，用户传 resume_from_node_index=1，
    期望返回 1（位置），而非去找 id==1 的节点。
    """
    nodes = [
        {"id": 1, "name": "a"},
        {"id": 100, "name": "b"},
        {"id": 3, "name": "c"},
    ]
    state = _state(resume_from_node_index=1)
    assert _resolve_target_index(state, nodes, "req") == 1


def test_resume_string_number_is_normalized():
    """字符串数字形式的索引需归一化为 int 后使用。"""
    state = _state(resume_from_node_index="2")
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 2


# ══════════════════════════════════════════════════
# 降级路径
# ══════════════════════════════════════════════════


def test_retry_id_not_found_falls_back_to_current():
    """retry_node_id 未匹配任何节点时，降级到 current_node_index。"""
    state = _state(current_node_index=2, retry_node_id=999)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 2


def test_retry_id_negative_falls_back_to_current():
    """负数 id 不匹配任何节点（id 通常为非负），降级 current。"""
    state = _state(current_node_index=1, retry_node_id=-1)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


def test_retry_id_bool_falls_back_to_current():
    """bool 是 int 的子类，但业务上不是合法 id，必须降级。

    若不特判，True 会被强制归一化为 1 并尝试匹配 id==1，产生
    静默错误。本测试锁定"bool 不进入匹配流程"的行为。
    """
    state = _state(current_node_index=1, retry_node_id=True)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1


def test_retry_id_float_falls_back_to_current():
    """float 不是合法 id，也不能 passthrough 到匹配循环。

    若 passthrough，下游可能触发 None/NaN 比较等未定义行为。
    """
    state = _state(current_node_index=0, retry_node_id=3.0)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0


def test_resume_out_of_range_falls_back_to_current():
    """resume 索引越界（大于等于 len(nodes)）时降级 current。"""
    state = _state(current_node_index=0, resume_from_node_index=999)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0


def test_resume_negative_falls_back_to_current():
    """负数索引不合法，降级 current。"""
    state = _state(current_node_index=2, resume_from_node_index=-1)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 2


def test_resume_bool_falls_back_to_current():
    """bool 同样是 resume 的非法输入，避免 True 被当作 index=1。"""
    state = _state(current_node_index=0, resume_from_node_index=True)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0


def test_resume_float_falls_back_to_current():
    """float 不是合法索引——nodes[1.0] 会在 CPython 中 TypeError。"""
    state = _state(current_node_index=0, resume_from_node_index=1.0)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 0


# ══════════════════════════════════════════════════
# 边界防御
# ══════════════════════════════════════════════════


def test_empty_nodes_returns_zero():
    """空节点列表时无任何指针可匹配，返回 0（调用方负责越界判断）。"""
    state = _state()
    assert _resolve_target_index(state, [], "req") == 0


def test_empty_nodes_with_retry_id_returns_current():
    """空节点列表 + retry_node_id 存在时，id 匹配必然失败，降级 current。"""
    state = _state(current_node_index=0, retry_node_id=1)
    assert _resolve_target_index(state, [], "req") == 0


def test_missing_pointer_fields_treated_as_none():
    """当指针字段根本未写入 state 时，等同于 None，走 current 分支。

    这是防御性回归：create_initial_state 之外的路径（如从旧 checkpoint
    反序列化）可能不含这些字段，_resolve_target_index 必须容忍。
    """
    state = _state(current_node_index=1)
    # 显式删除字段，模拟"旧版本 state 反序列化"的场景。
    state.pop("retry_node_id", None)
    state.pop("resume_from_node_index", None)
    assert _resolve_target_index(state, NODES_1_2_3, "req") == 1
