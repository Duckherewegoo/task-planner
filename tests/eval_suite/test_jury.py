"""评委团聚合测试（不调真实 LLM）。"""

from task_planner.eval_suite.jury.calibrated_judge import CalibratedJudge


def test_agreement_high():
    assert CalibratedJudge._agreement([8.0, 8.1, 7.9]) > 0.95


def test_agreement_low():
    assert CalibratedJudge._agreement([2.0, 9.0, 5.0]) < 0.7


def test_ci():
    lo, hi = CalibratedJudge._confidence_interval([8.0, 8.5, 9.0])
    assert lo is not None and hi is not None
    assert lo < 8.5 < hi
