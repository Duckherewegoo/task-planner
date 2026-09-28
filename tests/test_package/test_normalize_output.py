"""
test_normalize_output.py — 锁定 P0-2 修复

原问题：_normalize_llm_output 的 P1 分支返回 {"content": ...}，
       但 refine_node 期望的 key 是 "details"。
       导致 LLM 输出被静默丢弃，refine 结果退化成原始节点。
修复：返回 key 对齐 expected_keys（name / details / meta）。

⚠️ 任何人把返回的 "details" 改回 "content"，这些测试会立刻红。
"""
from __future__ import annotations

import pytest

from task_planner.infrastructure.llm_client import _normalize_llm_output


REFINE_KEYS = ["name", "details", "meta"]


class TestNormalizeLlmOutput:
    """P0-2 回归保护"""

    def test_already_conforming_passthrough(self):
        """已经是目标格式 → 直接透传"""
        raw = {
            "name": "准备食材",
            "details": "洗切",
            "meta": {"preconditions": []},
        }
        result = _normalize_llm_output(raw, REFINE_KEYS)
        assert result is raw or result == raw

    def test_result_format_maps_to_details(self):
        """⚠️ 关键回归：{"result": ...} → details，不是 content"""
        raw = {"result": "洗切西红柿", "notes": "注意去蒂"}
        result = _normalize_llm_output(raw, REFINE_KEYS)

        assert "details" in result, "P0-2 回归：result 未映射到 details"
        assert result["details"] == "洗切西红柿"
        assert "content" not in result, "不应返回 content 字段"
        assert result["meta"]["notes"] == "注意去蒂"

    def test_detail_format_maps_to_details(self):
        """{"detail": ...} → details"""
        raw = {"detail": "热油炒散", "meta": {"preconditions": ["锅热"]}}
        result = _normalize_llm_output(raw, REFINE_KEYS)

        assert result["details"] == "热油炒散"
        assert result["meta"]["preconditions"] == ["锅热"]

    def test_non_dict_input_returns_empty(self):
        """非 dict 输入 → {}"""
        assert _normalize_llm_output([1, 2, 3], REFINE_KEYS) == {}  # type: ignore
        assert _normalize_llm_output("string", REFINE_KEYS) == {}   # type: ignore
        assert _normalize_llm_output(None, REFINE_KEYS) == {}        # type: ignore

    def test_meta_not_dict_resets_to_empty(self):
        """meta 是字符串 → 重置为 {}"""
        raw = {"result": "abc", "meta": "this is a string"}
        result = _normalize_llm_output(raw, REFINE_KEYS)
        assert isinstance(result["meta"], dict)
        assert result["meta"] == {}

    def test_result_not_string_skips_p1(self):
        """result 非字符串 → 跳过 P1，走 P3 兜底"""
        raw = {"result": [1, 2, 3], "other": "val"}
        result = _normalize_llm_output(raw, REFINE_KEYS)
        # 应该走兜底，原样返回
        assert "details" not in result or result.get("result") == [1, 2, 3]

    def test_unknown_format_passthrough(self):
        """完全无法识别 → 原样透传（P3）"""
        raw = {"weird_key": "weird_val"}
        result = _normalize_llm_output(raw, REFINE_KEYS)
        assert result == raw

    def test_result_with_name_preserves_name(self):
        """P1 分支：LLM 同时给了 name 和 result"""
        raw = {"name": "自定义名", "result": "洗切"}
        result = _normalize_llm_output(raw, REFINE_KEYS)
        assert result["name"] == "自定义名"
        assert result["details"] == "洗切"
