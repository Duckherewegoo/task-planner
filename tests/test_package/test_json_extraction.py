"""
test_json_extraction.py — 锁定 P0-1 修复

原问题：手写引号状态机在处理转义字符时方向反了，
       含 \" 的字符串会导致 in_string 状态错乱，提取失败。
修复：改用 json.JSONDecoder.raw_decode，正确性由标准库保证。

⚠️ 任何人把 _extract_tail_json 改回手写状态机，这些测试会立刻红。
"""
from __future__ import annotations

import json
import pytest

from task_planner.infrastructure.llm.json_utils import extract_tail_json as _extract_tail_json


class TestExtractTailJson:
    """P0-1 回归保护"""

    def test_pure_json(self):
        """纯 JSON 直接返回"""
        text = '{"name": "test", "details": "abc"}'
        result = _extract_tail_json(text)
        assert result is not None
        assert json.loads(result) == {"name": "test", "details": "abc"}

    def test_thinking_prefix_plus_json(self):
        """思考前缀 + JSON → 只提取 JSON 部分"""
        text = '思考过程：我先想一下...\n最终答案是：{"success": true, "detail": "完成"}'
        result = _extract_tail_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["detail"] == "完成"

    def test_escaped_quotes_in_string(self):
        """⚠️ 关键回归：字符串内含转义引号，手写状态机会翻车"""
        # 注意这里是 raw string，包含真正的 \" 转义
        text = r'思考过程...{"reason": "包含\"和}的字符串", "ok": true}'
        result = _extract_tail_json(text)
        assert result is not None, "P0-1 回归：含转义引号的 JSON 提取失败"
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert '含\"和}' in parsed["reason"] or '含"和}' in parsed["reason"]

    def test_nested_braces(self):
        """嵌套花括号"""
        text = '思考过程...{"a": {"b": {"c": 1}}, "d": 2}'
        result = _extract_tail_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["a"]["b"]["c"] == 1

    def test_multiple_json_blocks_returns_last(self):
        """有多个 JSON 块时，返回最后一个（尾部提取语义）"""
        text = '{"first": 1} 中间文字 {"last": 2}'
        result = _extract_tail_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert "last" in parsed, f"应提取最后一个 JSON，实际拿到: {parsed}"

    def test_no_json_returns_none(self):
        """完全无 JSON → None"""
        assert _extract_tail_json("这里没有任何 JSON") is None

    def test_empty_input(self):
        """空串/None → None"""
        assert _extract_tail_json("") is None
        assert _extract_tail_json(None) is None  # type: ignore

    def test_malformed_json_returns_none(self):
        """只有花括号但内部非法 → None"""
        assert _extract_tail_json("{这不是JSON}") is None

    def test_unicode_content(self):
        """含中文/emoji 的 JSON"""
        text = '思考过程...{"emoji": "🎉", "中文": "值"}'
        result = _extract_tail_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["emoji"] == "🎉"
        assert parsed["中文"] == "值"
