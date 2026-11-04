import pytest
from task_planner.eval_suite.scorers.deterministic import score_deterministic
from task_planner.eval_suite.types import AgentTrace, GoldenCase

@pytest.fixture
def base_golden():
    return GoldenCase(
        case_id="case-1",
        user_input="帮我查天气并画图",
        expected_intent={"needs_planning": True, "category": "research"},
        expected_plan={
            "nodes": [{"id": 1, "name": "查天气"}, {"id": 2, "name": "画图"}],
            "edges": [{"from": 1, "to": 2}]
        }
    )

def test_score_perfect_match(base_golden):
    trace = AgentTrace(
        case_id="case-1",
        user_input="帮我查天气并画图", # 补充必填项
        thread_id="t1",
        mock_mode=True,
        intent={"needs_planning": True, "category": "research"},
        plan={
            "nodes": [{"id": 1, "name": "查天气"}, {"id": 2, "name": "画图"}],
            "edges": [{"from": 1, "to": 2}]
        }
    )
    scores = score_deterministic(trace, base_golden)
    assert scores.intent_match == 1.0
    assert scores.node_count_accuracy == 1.0
    assert scores.edge_count_accuracy == 1.0
    assert scores.structure_valid is True
    assert scores.node_name_overlap == 1.0

def test_score_intent_mismatch(base_golden):
    trace = AgentTrace(
        case_id="case-1", 
        user_input="帮我查天气并画图", # 补充必填项
        thread_id="t2", 
        mock_mode=True,
        intent={"needs_planning": False, "category": "chat"}, 
        plan={} 
    )
    scores = score_deterministic(trace, base_golden)
    assert scores.intent_match == 0.0

def test_score_partial_nodes(base_golden):
    trace = AgentTrace(
        case_id="case-1", 
        user_input="帮我查天气并画图", # 补充必填项
        thread_id="t3", 
        mock_mode=True,
        intent={"needs_planning": True, "category": "research"},
        plan={"nodes": [{"id": 1, "name": "查天气"}], "edges": []}
    )
    scores = score_deterministic(trace, base_golden)
    assert scores.node_count_accuracy == 0.5 
    assert scores.edge_count_accuracy == 0.0
