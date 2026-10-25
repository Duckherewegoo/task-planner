"""确定性评分测试（纯函数）。"""

from dataclasses import replace

from task_planner.eval_suite.scorers.deterministic import score_deterministic


def test_perfect(fake_trace, fake_golden):
    s = score_deterministic(fake_trace, fake_golden)
    assert s.intent_match == 1.0
    assert s.node_count_accuracy == 1.0
    assert s.edge_count_accuracy == 1.0
    assert s.structure_valid is True
    assert s.overall >= 0.95


def test_wrong_intent(fake_trace, fake_golden):
    bad = replace(fake_trace, intent={"needs_planning": False})
    s = score_deterministic(bad, fake_golden)
    assert s.intent_match == 0.0


def test_cyclic_rejected(fake_trace, fake_golden):
    bad_plan = {
        "nodes": [{"id": i, "name": f"n{i}"} for i in (1, 2, 3)],
        "edges": [
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
            {"from": 3, "to": 1},
        ],
    }
    bad = replace(fake_trace, plan=bad_plan)
    s = score_deterministic(bad, fake_golden)
    assert s.structure_valid is False
