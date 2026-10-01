"""
view_model.py — 领域状态 → 前端 DTO 的投影层

把「哪些字段要给前端看」这个关注点从 state.py / config.py 收敛到这里。
前端新增字段、v2 API 变更，都在这一层完成。
"""
from __future__ import annotations

from typing import Any


# 面向前端的字段白名单（除 nodes/edges 单独处理外）
FRONTEND_FIELDS: tuple[str, ...] = (
    "task_id",
    "svg",
    "flowchart_html",
    "direct_response",
    "status_text",
    "steps",
    "error",
    "cancel_requested",
    "current_node_index",
    "node_results",
    "needs_planning",
    "view_mode",
)


def project_to_frontend(state: dict[str, Any]) -> dict[str, Any]:
    """
    从完整 TaskState 投影出前端视图。
    新增/删除前端字段时，只改这个函数。
    """
    view = {k: state.get(k) for k in FRONTEND_FIELDS}
    view["nodes"] = state.get("nodes") or []
    view["edges"] = state.get("edges") or []
    return view

__all__ = ["FRONTEND_FIELDS", "project_to_frontend"]
