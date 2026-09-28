"""
test_evaluators.py — 锁定 evaluators 的 3 个 P0 修复
"""
from __future__ import annotations

import pytest

from tests.eval_suite.harness.evaluators import Evaluators, _tokenize


class TestPlanNodeCountAccuracy:
    """P0-1 回归：结果恒在 [0, 1]"""

    def test_zero_zero(self):
        assert Evaluators.plan_node_count_accuracy(0, []) == 1.0

    def test_zero_expected_many_actual(self):
        """关键回归：expected=0, actual=4 不能再返回 -3.0"""
        r = Evaluators.plan_node_count_accuracy(0, [{"id": 1}, {"id": 2}])
        assert r == 0.0, f"应得 0.0，实际 {r}"

    def test_perfect_match(self):
        assert Evaluators.plan_node_count_accuracy(4, [1, 2, 3, 4]) == 1.0

    def test_close_count(self):
        r = Evaluators.plan_node_count_accuracy(4, [1, 2, 3])
        assert 0.5 < r < 1.0

    def test_all_results_in_range(self):
        """所有可能组合都在 [0, 1]"""
        for exp in range(0, 10):
            for act in range(0, 10):
                r = Evaluators.plan_node_count_accuracy(exp, list(range(act)))
                assert 0.0 <= r <= 1.0, f"exp={exp}, act={act}, r={r}"


class TestResponseRelevance:
    """P0-2 回归：中文相关性不再为 0"""

    def test_chinese_overlap(self):
        r = Evaluators.response_relevance("西红柿切块，鸡蛋打散", "西红柿炒鸡蛋")
        assert r > 0.0, "中文相关性不能为 0"

    def test_english_overlap(self):
        r = Evaluators.response_relevance("hello world foo", "hello world")
        assert r == 1.0

    def test_empty_input(self):
        assert Evaluators.response_relevance("anything", "") == 1.0

    def test_no_overlap(self):
        r = Evaluators.response_relevance("completely different", "你好世界")
        assert r == 0.0


class TestAccuracy:
    """P0-3 回归：词序不同不应得 0 分"""

    def test_word_order_difference(self):
        """关键回归：语序不同但内容相同"""
        r = Evaluators.accuracy("西红柿炒鸡蛋", "鸡蛋炒西红柿")
        assert r > 0.3, f"语序不同得分过低: {r}"

    def test_exact_same(self):
        assert Evaluators.accuracy("abc", "abc") == 1.0

    def test_empty_both(self):
        assert Evaluators.accuracy("", "") == 1.0

    def test_one_empty(self):
        assert Evaluators.accuracy("abc", "") == 0.0


class TestExactMatch:
    def test_case_insensitive(self):
        assert Evaluators.exact_match("Hello", "hello") is True

    def test_punct_ignored(self):
        assert Evaluators.exact_match("西红柿炒蛋。", "西红柿炒蛋") is True

    def test_real_difference(self):
        assert Evaluators.exact_match("abc", "abd") is False


class TestCheckAcyclic:
    def test_simple_dag(self):
        nodes = [{"id": 1}, {"id": 2}, {"id": 3}]
        edges = [{"from": 1, "to": 2}, {"from": 2, "to": 3}]
        assert Evaluators.check_acyclic(nodes, edges) is True

    def test_cycle(self):
        nodes = [{"id": 1}, {"id": 2}]
        edges = [{"from": 1, "to": 2}, {"from": 2, "to": 1}]
        assert Evaluators.check_acyclic(nodes, edges) is False

    def test_phantom_edge_ignored(self):
        """边指向不存在节点 → 静默跳过，不算环"""
        nodes = [{"id": 1}]
        edges = [{"from": 1, "to": 999}]
        assert Evaluators.check_acyclic(nodes, edges) is True


class TestTokenize:
    def test_chinese_bigram(self):
        tokens = _tokenize("西红柿炒鸡蛋")
        assert "西红" in tokens
        assert "红柿" in tokens
        assert "鸡蛋" in tokens

    def test_english_word(self):
        tokens = _tokenize("Hello World")
        assert "hello" in tokens
        assert "world" in tokens

    def test_mixed(self):
        tokens = _tokenize("Python 编程")
        assert "python" in tokens
        assert "编程" in tokens
