"""routes.py — 路由函数（纯计算，无 IO）"""
from __future__ import annotations

from task_planner.core.graph.state import (
    TaskState,
    UserAction,
)


def route_after_intent(state: TaskState) -> str:
    if state.get("cancel_requested"):
        return "cancel"
    if not state.get("needs_planning", True):
        return "direct_answer"
    return "plan"


def route_after_execute(state: TaskState) -> str:
    action = state.get("user_action")
    if action == UserAction.CANCEL:
        return "cancel"
    if action == UserAction.MODIFY:
        return "intent"
    if action == UserAction.RETRY_NODE:
        return "execute"
    idx = state.get("current_node_index", 0)
    if idx >= len(state.get("nodes", [])):
        return "finish"
    return "execute"


def route_after_render(state: TaskState) -> str:
    return "execute"
