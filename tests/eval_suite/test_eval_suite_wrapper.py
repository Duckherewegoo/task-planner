"""
Pytest 包装器：将评估套件集成到现有 pytest 测试流程中
"""

import pytest
import asyncio
from .dataset_manager import DatasetManager
from .crucible_eval import CrucibleEvaluator, create_default_dataset
from .harness.evaluators import Evaluators


@pytest.mark.slow
@pytest.mark.integration
def test_eval_suite_basic():
    """基本测试：确保评估套件能运行并返回结果"""
    cases = create_default_dataset()[:2]  # 只取前2个以加快速度
    evaluator = CrucibleEvaluator(cases, max_concurrent=1, timeout=60)
    summary = asyncio.run(evaluator.run())
    assert summary["total"] == 2
    assert "success_rate" in summary
    assert "avg_accuracy" in summary


@pytest.mark.parametrize("case", create_default_dataset())
def test_eval_single_case(case):
    """测试单个案例（用于快速调试）"""
    # 跳过太复杂的
    if case.complexity == "complex":
        pytest.skip("Skipping complex case for fast test")
    evaluator = CrucibleEvaluator([case], max_concurrent=1, timeout=120)
    summary = asyncio.run(evaluator.run())
    assert summary["total"] == 1
    result = evaluator.results[0]
    assert "metrics" in result
    assert "accuracy" in result["metrics"]


@pytest.mark.unit
def test_evaluators():
    """测试评估器单元"""
    assert Evaluators.accuracy("hello", "hello") == 1.0
    assert Evaluators.exact_match("hello", "hello") is True
    assert Evaluators.contains_keyword("hello world", ["world"]) is True
    assert Evaluators.plan_node_count_accuracy(5, [{"id": 1}, {"id": 2}]) == 1.0 - (3/5)
    # 测试环检测
    nodes = [{"id": 1}, {"id": 2}]
    edges = [{"from": 1, "to": 2}]
    assert Evaluators.check_acyclic(nodes, edges) is True
    edges_cycle = [{"from": 1, "to": 2}, {"from": 2, "to": 1}]
    assert Evaluators.check_acyclic(nodes, edges_cycle) is False
