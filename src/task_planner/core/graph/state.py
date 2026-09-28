"""
state.py — LangGraph 状态定义（P2 修复版）
"""
from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, Literal

from langgraph.graph import MessagesState


class UserAction(StrEnum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    MODIFY = "modify"
    RETRY_NODE = "retry_node"


UserActionLiteral = Literal[*UserAction]


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class NodeResult(dict):
    """
    单个节点的执行结果。
    用 TypedDict 会更好，但为兼容现有 dict 写入，先用 dict 别名。
    """


class TaskState(MessagesState):
    """LangGraph 全局状态。继承 MessagesState 自带 messages 字段。"""

    # ── 用户输入 ──
    user_input: str
    thread_id: str

    # ── 意图识别 ──
    intent: dict[str, Any]
    needs_planning: bool

    # ── 规划结果 ──
    plan: dict[str, Any]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]

    # ── 节点细化 ──
    refined_nodes: list[dict[str, Any]]

    # ── 数据库关联 ──
    task_id: str

    # ── 执行进度 ──
    current_node_index: int
    # ✅ P0 修复：加 reducer，节点只需返回增量，LangGraph 自动拼接
    node_results: Annotated[list[dict[str, Any]], operator.add]

    # ── 渲染产物 ──
    svg: str
    flowchart_html: str

    # ── 直接回答 ──
    direct_response: str

    # ── 状态控制 ──
    cancel_requested: bool
    error: str
    status_text: str
    # ✅ P0 修复：加 reducer，append-only
    steps: Annotated[list[str], operator.add]

    # ── 用户指令（interrupt / resume 用）──
    user_action: UserAction | None
    modified_input: str | None
    retry_node_id: int | None

    # ── 断点续传辅助字段 ──
    resume_from_node_index: int | None

    # ✅ P0 修复：view_mode 不属于领域状态，已移除。
    #    改为 run_task_stream(..., view_mode=...) 的运行时参数。
    #    如果你暂时不想动 agent.py，就先把这行注释掉，别删，留着也行。

    # ── Schema 版本（checkpoint 兼容性）──
    schema_version: int


def validate_user_action(action: str) -> UserAction:
    """将外部输入安全转换为合法 UserAction，非法值立即报错。"""
    try:
        return UserAction(action)
    except ValueError:
        valid_values = [e.value for e in UserAction]
        raise ValueError(
            f"Invalid user action '{action}'. Expected one of {valid_values}"
        ) from None
