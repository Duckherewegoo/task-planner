"""
Pytest 包装器：将评估套件集成到现有 pytest 测试流程中

Changelog:
  ✅ P0-1：修复 collection ImportError——相对导入改为包绝对导入 + 兜底
  ✅ P0-2：顶部强制 USE_MOCK_LLM=true，避免误耗 API 配额
  ✅ P0-3：改用 pytest.mark.asyncio（pytest-asyncio 已启用 auto 模式）
  ✅ P1-1：注册 unit / eval marker（在 pyproject.toml 里）
  ✅ P1-2：parametrize 加 ids，测试输出可读
  ✅ P1-3：单 case 测试用 asyncio.wait_for 加超时兜底
  ✅ P2-1：用 module 级 fixture 只构造一次数据集
  ✅ P2-2：test_eval_suite_basic 断言更细
"""

import asyncio
import os

import pytest

# ✅ P0-2 修复：在任何 task_planner 模块被 import 之前就固定 Mock 模式。
#    conftest.py 里也有 setdefault，这里再设一次是双保险——
#    因为这个文件可能被单独跑（`pytest test_eval_suite_wrapper.py`），
#    那时 conftest 的 fixture 还没执行。
os.environ.setdefault("USE_MOCK_LLM", "true")
os.environ.setdefault("CHECKPOINTER_TYPE", "memory")
os.environ.setdefault("MONGO_HOST", "localhost")


# ✅ P0-1 修复：优先用包绝对导入（pytest rootdir 模式），
#    失败时回退到相对导入（eval_suite 被当作独立包安装的场景）。
try:
    from tests.eval_suite.dataset_manager import DatasetManager
    from tests.eval_suite.crucible_eval import (
    CrucibleEvaluator,
    create_default_dataset,
)
    from tests.eval_suite.harness.evaluators import Evaluators
except ImportError:
    from .dataset_manager import DatasetManager
    from .crucible_eval import (
    CrucibleEvaluator,
    create_default_dataset,
)
    from .harness.evaluators import Evaluators


# ── 模块级 fixture：只构造一次数据集 ──
@pytest.fixture(scope="module")
def default_dataset():
    return create_default_dataset()


@pytest.fixture(scope="module")
def fast_dataset(default_dataset):
    """只取简单/中等 case，避免 CI 太慢"""
    return [c for c in default_dataset if c.complexity != "complex"]


# ══════════════════════════════════════════════════
#  集成测试
# ══════════════════════════════════════════════════

@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.eval
@pytest.mark.asyncio
async def test_eval_suite_basic(fast_dataset):
    """
    基本测试：确保评估套件能运行并返回结构完整的 summary。

    ✅ P2-2 修复：断言更细，不只是字段存在。
    """
    cases = fast_dataset[:2]
    evaluator = CrucibleEvaluator(
        cases, max_concurrent=1, timeout=60,
    )

    # ✅ P1-3 修复：给整个 run 加超时兜底
    summary = await asyncio.wait_for(evaluator.run(), timeout=180)

    # 结构断言
    assert summary["total"] == 2
    assert "success_rate" in summary
    assert "avg_accuracy" in summary
    assert "avg_relevance" in summary
    assert "by_category" in summary
    assert "timestamp" in summary

    # 语义断言
    assert 0.0 <= summary["success_rate"] <= 1.0
    assert 0.0 <= summary["avg_accuracy"] <= 1.0

    # 每个 case 都有完整 metrics
    assert len(evaluator.results) == 2
    for r in evaluator.results:
        assert "metrics" in r
        assert "success" in r["metrics"]
        assert "accuracy" in r["metrics"]


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    create_default_dataset(),
    # ✅ P1-2 修复：用 case.id 作为测试 ID，输出可读
    ids=lambda c: c.id,
)
async def test_eval_single_case(case):
    """测试单个案例（用于快速调试）"""
    # 跳过 complex case（耗时太久）
    if case.complexity == "complex":
        pytest.skip(f"Skipping complex case {case.id} for fast test")

    evaluator = CrucibleEvaluator(
        [case], max_concurrent=1, timeout=60,
    )

    summary = await asyncio.wait_for(evaluator.run(), timeout=120)

    assert summary["total"] == 1
    assert len(evaluator.results) == 1

    result = evaluator.results[0]
    assert "metrics" in result
    assert "accuracy" in result["metrics"]
    assert "relevance" in result["metrics"]
    assert "success" in result["metrics"]


# ══════════════════════════════════════════════════
#  纯单元测试（不依赖 Agent / DB / 网络）
# ══════════════════════════════════════════════════

@pytest.mark.unit
class TestEvaluators:
    """评估器单元测试"""

    def test_accuracy_identical(self):
        assert Evaluators.accuracy("hello", "hello") == 1.0

    def test_accuracy_chinese_word_order(self):
        """✅ 锁定 P0-3：语序不同不应得 0 分"""
        r = Evaluators.accuracy("西红柿炒鸡蛋", "鸡蛋炒西红柿")
        assert r > 0.3, f"语序不同得分过低: {r}"

    def test_exact_match(self):
        assert Evaluators.exact_match("hello", "hello") is True
        assert Evaluators.exact_match("Hello", "hello") is True  # 大小写

    def test_contains_keyword(self):
        assert Evaluators.contains_keyword("hello world", ["world"]) is True
        assert Evaluators.contains_keyword("hello world", ["python"]) is False

    def test_plan_node_count_accuracy_exact(self):
        """✅ 锁定 P0-1：精确匹配应得 1.0"""
        assert Evaluators.plan_node_count_accuracy(
            5, [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}]
        ) == 1.0

    def test_plan_node_count_accuracy_no_negative(self):
        """✅ 锁定 P0-1：结果恒在 [0, 1]，不再是负数"""
        r = Evaluators.plan_node_count_accuracy(0, [{"id": 1}, {"id": 2}])
        assert r == 0.0, f"expected=0, actual=2 应得 0.0，实际 {r}"
        assert 0.0 <= r <= 1.0

    def test_check_acyclic_simple(self):
        nodes = [{"id": 1}, {"id": 2}]
        edges = [{"from": 1, "to": 2}]
        assert Evaluators.check_acyclic(nodes, edges) is True

    def test_check_acyclic_cycle(self):
        nodes = [{"id": 1}, {"id": 2}]
        edges = [{"from": 1, "to": 2}, {"from": 2, "to": 1}]
        assert Evaluators.check_acyclic(nodes, edges) is False

    def test_response_relevance_chinese(self):
        """✅ 锁定 P0-2：中文相关性不为 0"""
        r = Evaluators.response_relevance(
            "西红柿切块，鸡蛋打散", "西红柿炒鸡蛋"
        )
        assert r > 0.0, "中文相关性不能为 0"
