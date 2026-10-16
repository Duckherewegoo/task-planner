"""共享 fixture。"""
import pytest

from task_planner.eval_suite.types import (
    AgentTrace,
    Difficulty,
    GoldenCase,
)


@pytest.fixture
def fake_golden():
    return GoldenCase(
        case_id="t1", user_input="教我番茄炒蛋",
        expected_plan={
            "nodes": [
                {"id": 1, "name": "备料", "detail": "x"},
                {"id": 2, "name": "炒蛋", "detail": "x"},
                {"id": 3, "name": "出锅", "detail": "x"},
            ],
            "edges": [
                {"from": 1, "to": 2, "label": ""},
                {"from": 2, "to": 3, "label": ""},
            ],
        },
        expected_intent={"needs_planning": True, "category": "cooking"},
        tags=("cooking",), difficulty=Difficulty.SIMPLE,
    )


@pytest.fixture
def fake_trace(fake_golden):
    return AgentTrace(
        case_id=fake_golden.case_id, user_input=fake_golden.user_input,
        thread_id="t", intent={"needs_planning": True, "category": "cooking"},
        plan=fake_golden.expected_plan,
        node_states={"1": "done", "2": "done", "3": "done"},
        steps=["a", "b"], duration_sec=1.5,
    )
