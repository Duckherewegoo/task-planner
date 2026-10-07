"""
sanitize.py — 横切关注点：脱敏、用户视图过滤、取消事件获取。
所有节点共用，不依赖其他 nodes 子模块。

Changelog:
  ── v1 ──
  ✅ SafeNodeView + sanitize_node_for_user / sanitize_nodes_for_user
  ✅ sanitize_input（敏感信息脱敏）
  ✅ get_cancel_event（本地实现）

  ── v2 ──
  ✅ P1-1：get_cancel_event 改为从 utils.context 转发，
           消除与 utils/context.py 的重复定义。
           唯一真源在 context.py，未来改行为只需改一处。
"""
from __future__ import annotations

import hashlib
from typing import (
    Any,
    TypedDict,
    cast,
)

from task_planner.infrastructure.constants import USER_VISIBLE_NODE_FIELDS
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.regexes import SENSITIVE_PATTERNS

# ✅ P1-1：唯一真源
from task_planner.utils.context import get_cancel_event

logger = get_logger(__name__)


class SafeNodeView(TypedDict, total=False):
    """面向前端的节点视图"""
    id: int
    name: str
    details: str
    status: str
    result: str


def sanitize_node_for_user(node: dict[str, Any]) -> SafeNodeView:
    """过滤掉内部 debug/meta 字段"""
    return cast(
        SafeNodeView,
        {k: v for k, v in node.items() if k in USER_VISIBLE_NODE_FIELDS},
    )


def sanitize_nodes_for_user(nodes: list[dict[str, Any]]) -> list[SafeNodeView]:
    return [sanitize_node_for_user(n) for n in nodes]


def sanitize_input(text: str, *, audit_log: bool = True) -> str:
    """
    敏感信息脱敏。
    匹配到敏感模式时打一条 audit 日志（只记 hash，不记原文）。
    """
    original_hash = hashlib.sha256(text.encode()).hexdigest() if audit_log else None
    sanitized = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    if audit_log and sanitized != text:
        logger.info(
            "[Audit] Input redacted | hash=%s | sanitized_len=%d",
            original_hash,
            len(sanitized),
        )
    return sanitized


__all__ = [
    "SafeNodeView",
    "sanitize_node_for_user",
    "sanitize_nodes_for_user",
    "sanitize_input",
    "get_cancel_event",
]
