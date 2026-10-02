"""
test_config_contract.py — 锁定配置层的一致性契约

锁定的 P0：
- P0-1：DEBUG 默认 False（生产安全）
- P0-2：NODE_STATUS_CODE_MAP 与 TASK_STATUS 对齐
- P1-1：INTENT_PROMPT 白名单与 VALID_INTENT_CATEGORIES 对齐
"""
from __future__ import annotations

import os

import pytest


class TestStatusContract:

    def test_node_status_map_has_all_codes(self):
        """NODE_STATUS_CODE_MAP 覆盖 TASK_STATUS 的所有状态码"""
        from task_planner.infrastructure.constants import (
    NODE_STATUS_CODE_MAP,
    TASK_STATUS,
)
        for name, code in TASK_STATUS.items():
            assert code in NODE_STATUS_CODE_MAP, \
                f"P0-2 回归：status code {code} ({name}) 不在 NODE_STATUS_CODE_MAP"

    def test_timeout_maps_to_timeout(self):
        """⚠️ 关键回归：status=4 必须映射为 timeout，不是 skipped"""
        from task_planner.infrastructure.constants import NODE_STATUS_CODE_MAP
        assert NODE_STATUS_CODE_MAP[4] == "timeout", \
            "P0-2 回归：4 应为 timeout"

    def test_skipped_maps_to_skipped(self):
        """status=5 必须映射为 skipped"""
        from task_planner.infrastructure.constants import NODE_STATUS_CODE_MAP
        assert NODE_STATUS_CODE_MAP[5] == "skipped", \
            "P0-2 回归：5 应为 skipped"

    def test_all_maps_have_consistent_length(self):
        """STATUS_TEXT / STATUS_COLOR / STATUS_BORDER 数量一致"""
        from task_planner.infrastructure.constants import (
    STATUS_BORDER,
    STATUS_COLOR,
    STATUS_ICONS,
    STATUS_TEXT,
)
        assert len(STATUS_TEXT) == len(STATUS_COLOR) == len(STATUS_BORDER) == len(STATUS_ICONS)


class TestIntentContract:

    def test_consultation_in_whitelist(self):
        """P1-1：consultation 应在白名单"""
        from task_planner.infrastructure.constants import VALID_INTENT_CATEGORIES
        assert "consultation" in VALID_INTENT_CATEGORIES

    def test_prompt_mentions_consultation(self):
        """P1-1：prompt schema 必须列出 consultation"""
        from task_planner.infrastructure.prompts.loader import INTENT_PROMPT
        assert "consultation" in INTENT_PROMPT.template, \
            "P1-1 回归：prompt 里没列 consultation，LLM 不会输出该分类"


class TestSecurityDefault:

    def test_debug_default_is_false(self):
        """P0-1：DEBUG 默认必须是 False（生产安全）"""
        from task_planner.infrastructure.cog import hub
        assert hub.dev.DEBUG is False, \
            "P0-1 回归：DEBUG 默认应为 False"

class TestPromptTemplate:

    def test_all_prompts_are_template(self):
        """所有 prompt 都应是 string.Template 实例"""
        from string import Template
        from task_planner.infrastructure.prompts.loader import (
    EXECUTE_NODE_PROMPT,
    INTENT_PROMPT,
    NODE_REFINE_PROMPT,
    PLANNER_PROMPT,
)
        for name, p in [
            ("INTENT_PROMPT", INTENT_PROMPT),
            ("PLANNER_PROMPT", PLANNER_PROMPT),
            ("NODE_REFINE_PROMPT", NODE_REFINE_PROMPT),
            ("EXECUTE_NODE_PROMPT", EXECUTE_NODE_PROMPT),
        ]:
            assert isinstance(p, Template), f"{name} 不是 Template"

    def test_skip_planning_patterns_are_compiled(self):
        """SKIP_PLANNING_PATTERNS 应全是编译后的正则"""
        import re
        from task_planner.infrastructure.regexes import SKIP_PLANNING_PATTERNS
        for p in SKIP_PLANNING_PATTERNS:
            assert isinstance(p, re.Pattern)
