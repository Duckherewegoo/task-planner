"""test_eval_suite.py — 评估套件的pytest入口（默认不跑）"""
import pytest
from pathlib import Path

from task_planner.eval.datasets.loader import load_golden_cases
from task_planner.eval.runners.agent_runner import run_agent
from task_planner.eval.scorers.deterministic import score_deterministic


@pytest.mark.eval
@pytest.mark.asyncio
async def test_golden_cases_deterministic_mock():
    """Mock模式下，所有黄金标准用例的确定性评分应 ≥ 0.6"""
    cases = load_golden_cases()
    assert len(cases) > 0, "黄金标准数据集为空"

    for case in cases:
        trace = await run_agent(case.case_id, case.user_input, use_mock=True)
        scores = score_deterministic(trace, case)
        assert scores.overall >= 0.6, (
            f"{case.case_id}: 确定性评分 {scores.overall:.2f} < 0.6 "
            f"(intent={scores.intent_match}, "
            f"node={scores.node_count_accuracy}, "
            f"edge={scores.edge_count_accuracy}, "
            f"struct={scores.structure_valid})"
        )
