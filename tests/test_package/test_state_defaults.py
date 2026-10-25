"""state.py 的默认值契约——覆盖 needs_planning 与 retrieved_examples 字段。"""

import pytest

from task_planner.core.graph.state import create_initial_state


# 新增：测应用层行为，不测框架层默认值
def test_make_initial_state_normal_needs_planning_true():
    """普通输入 → needs_planning=True（让 intent 判断）"""
    from task_planner.services.agent.state import make_initial_state

    state = make_initial_state("帮我规划一个任务", "t-1")
    assert state["needs_planning"] is True


def test_make_initial_state_skip_planning_sets_false():
    """命中跳过正则 → needs_planning=False（直接回答）"""
    from task_planner.services.agent.state import make_initial_state

    # 假设 SKIP_PLANNING_PATTERNS 覆盖 "不用规划"——具体触发词见 regexes.py
    state = make_initial_state("不用规划，直接告诉我答案", "t-1")
    assert state["needs_planning"] is False
    assert state["direct_response"] == "__DIRECT_RESPONSE_PENDING__"


def test_make_initial_state_has_all_typed_dict_fields():
    """字段完整性——覆盖 make_initial_state 走的组合路径。"""
    from task_planner.core.graph.state import TaskState
    from task_planner.services.agent.state import make_initial_state

    state = make_initial_state("测试输入", "t-1")
    # 所有 TypedDict 声明的键（包括 NotRequired）都应存在
    for field in TaskState.__annotations__:
        assert field in state, f"make_initial_state 漏写字段: {field!r}"


def test_retrieved_examples_default_is_empty_list():
    """retrieved_examples 应被初始化为 []，供 retrieve 未启用时使用。"""
    state = create_initial_state(user_input="x", thread_id="t")
    assert state["retrieved_examples"] == []


def test_node_results_default_is_empty_list():
    state = create_initial_state(user_input="x", thread_id="t")
    assert state["node_results"] == []


def test_steps_default_is_empty_list():
    state = create_initial_state(user_input="x", thread_id="t")
    assert state["steps"] == []


def test_user_action_default_is_none():
    state = create_initial_state(user_input="x", thread_id="t")
    assert state["user_action"] is None


def test_retry_and_resume_pointers_default_to_none():
    state = create_initial_state(user_input="x", thread_id="t")
    assert state["retry_node_index"] is None
    assert state["resume_from_node_index"] is None


def test_schema_version_default():
    state = create_initial_state(user_input="x", thread_id="t")
    assert state["schema_version"] == 1


def test_kwargs_can_override_non_reserved_field():
    state = create_initial_state("x", "t", status_text="custom")
    assert state["status_text"] == "custom"


def test_reserved_kwargs_check_is_unreachable_via_python_binding():
    """
    文档测试：reserved 检查（user_input / thread_id / schema_version）在当前
    Python 参数绑定语义下不可达——显式参数无法通过 **kwargs 覆盖，
    Python 会在参数绑定阶段抛 TypeError。

    保留此测试以记录事实。如果未来 Python 改了参数绑定行为，
    这个测试会失败，提醒我们那个内部检查变得可达了。
    """
    with pytest.raises(TypeError):
        # Python 层面拦截 "multiple values for argument"
        create_initial_state("x", "t", user_input="y")
