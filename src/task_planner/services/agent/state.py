"""state.py — 初始状态构建 + 跳过规划判断"""
from __future__ import annotations

from typing import Any

from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.regexes import SKIP_PLANNING_PATTERNS

logger = get_logger(__name__)

_SKIP_PLANNING_PATTERNS = SKIP_PLANNING_PATTERNS


def should_skip_planning(user_input: str) -> bool:
    """显式拒绝规划的正则拦截（同步，纯计算）"""
    for pattern in _SKIP_PLANNING_PATTERNS:
        if pattern.search(user_input):
            logger.info("[Agent] 显式拒绝规划命中: %s", pattern.pattern)
            return True
    return False


def make_initial_state(user_input: str, thread_id: str) -> dict[str, Any]:
    """构建初始状态，含显式拒绝规划拦截"""
    skip_planning = should_skip_planning(user_input)

    if skip_planning:
        logger.info("[Agent] 关键词/正则拦截：用户明确要求跳过规划")

    return {
        "messages": [],
        "user_input": user_input,
        "thread_id": thread_id,
        "intent": {},
        "needs_planning": not skip_planning,
        "plan": {},
        "nodes": [],
        "edges": [],
        "refined_nodes": [],
        "task_id": "",
        "current_node_index": 0,
        "node_results": [],
        "svg": "",
        "flowchart_html": "",
        "direct_response": "__DIRECT_RESPONSE_PENDING__" if skip_planning else "",
        "cancel_requested": False,
        "error": "",
        "status_text": "",
        "steps": ["跳过规划：用户显式拒绝"] if skip_planning else [],
        "user_action": None,
        "modified_input": None,
        "retry_node_id": None,
        "resume_from_node_index": None,
        "schema_version": 1,
    }


__all__ = ["should_skip_planning", "make_initial_state"]
