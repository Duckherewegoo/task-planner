"""
test_initial_state.py — 锁定初始状态字段完整性

_make_initial_state 手写所有字段，容易漏。
TaskState 新增字段后，如果 _make_initial_state 没同步，会静默丢字段。

本测试通过反射对比 TaskState 的注解字段，检测漏写。
"""
from __future__ import annotations

import pytest

from task_planner.core.graph.state import TaskState
from task_planner.services.agent.state import make_initial_state as _make_initial_state


class TestMakeInitialState:

    def test_all_typed_dict_fields_present(self):
        """⚠️ 关键：TaskState 声明的字段都必须出现在 initial_state 中"""
        state = _make_initial_state("测试输入", "thread-1")

        # MessagesState 自动带 messages，其他字段都是本类声明
        expected = set(TaskState.__annotations__.keys())
        actual = set(state.keys())

        missing = expected - actual
        assert not missing, \
            f"initial_state 漏写字段: {missing}. " \
            f"请在 _make_initial_state 中补齐。"

    def test_schema_version_present(self):
        """P2 修复：schema_version 字段存在"""
        state = _make_initial_state("测试", "t1")
        assert "schema_version" in state
        assert isinstance(state["schema_version"], int)

    def test_skip_planning_hit(self):
        """显式拒绝规划 → needs_planning=False"""
        state = _make_initial_state("不需要规划，直接告诉我答案", "t1")
        assert state["needs_planning"] is False
        assert state["direct_response"] == "__DIRECT_RESPONSE_PENDING__"
        assert any("跳过规划" in s for s in state["steps"])

    def test_normal_input_needs_planning(self):
        """正常输入 → needs_planning=True"""
        state = _make_initial_state("帮我做番茄炒蛋", "t1")
        assert state["needs_planning"] is True
        assert state["direct_response"] == ""

    def test_thread_id_preserved(self):
        state = _make_initial_state("x", "my-thread")
        assert state["thread_id"] == "my-thread"
