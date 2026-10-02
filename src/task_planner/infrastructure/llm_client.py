"""
llm_client.py — 【兼容壳】
实现已迁至 task_planner.infrastructure.llm 包。
保留此文件仅为向后兼容，新代码请直接 import 自 .llm
"""
from __future__ import annotations

# 全部转发
from task_planner.infrastructure.llm import (
    direct_chat,
    recognize_intent,
    generate_plan,
    refine_node,
    execute_node_llm,
    get_llm_client,
    async_call_llm,
    LLMClientError,
    LLMTimeoutError,
    LLMCancelledError,
    LLMResponseError,
    extract_json,
    normalize_llm_output,
    _render_template,
)

# 兼容旧私有名
_extract_json = extract_json

__all__ = [
    "direct_chat",
    "recognize_intent",
    "generate_plan",
    "refine_node",
    "execute_node_llm",
    "get_llm_client",
    "async_call_llm",
    "LLMClientError",
    "LLMTimeoutError",
    "LLMCancelledError",
    "LLMResponseError",
    "extract_json",
    "normalize_llm_output",
    "_extract_json",
    "_render_template",
]

# ═══════════════════════════════════════════════════════════════════
#  向后兼容：旧私有名 alias（下个大版本删除）
# ═══════════════════════════════════════════════════════════════════
from task_planner.infrastructure.llm.json_utils import (
    extract_tail_json as _extract_tail_json,
    normalize_llm_output as _normalize_llm_output,
)
