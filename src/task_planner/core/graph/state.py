"""
state.py — LangGraph 状态定义（P2 修复版）
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, get_args

from langgraph.graph import MessagesState


class UserAction(StrEnum):
    CONTINUE = "continue"      # 确认当前步骤结果，继续执行下一节点
    CANCEL = "cancel"          # 终止整个图的执行
    MODIFY = "modify"          # 携带修改后的参数重新执行当前节点
    RETRY_NODE = "retry_node"  # 不修改参数，原样重试当前失败节点


# 需要纯 Literal 时仍可提取
UserActionLiteral = Literal[*UserAction]  # Python 3.12+ unpack


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class TaskState(MessagesState):
    """
    LangGraph 全局状态。

    继承 MessagesState → 自带 messages: list[BaseMessage] 字段，
    支持 LangGraph checkpointer 自动持久化对话历史。
    """

    # ── 用户输入 ──────────────────────────────
    user_input: str
    thread_id: str

    # ── 意图识别 ──────────────────────────────
    intent: dict[str, Any]
    needs_planning: bool

    # ── 规划结果 ──────────────────────────────
    plan: dict[str, Any]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]

    # ── 节点细化 ──────────────────────────────
    refined_nodes: list[dict[str, Any]]

    # ── 数据库关联 ────────────────────────────
    task_id: str

    # ── 执行进度 ──────────────────────────────
    current_node_index: int
    node_results: list[dict[str, Any]]
    """每个元素: {"node_id": int, "success": bool, "detail": str}"""

    # ── 渲染产物 ──────────────────────────────
    svg: str
    flowchart_html: str
    """Cytoscape / PyVis 渲染后的 HTML 片段"""

    # ── 直接回答（无需规划的快捷路径）──────────
    direct_response: str

    # ── 状态控制 ──────────────────────────────
    cancel_requested: bool
    error: str
    status_text: str
    steps: list[str]
    """人类可读的执行步骤日志，用于前端状态栏展示"""

    # ── 用户指令（interrupt / resume 用）──────
    user_action: UserAction | None
    modified_input: str | None
    retry_node_id: int | None

    # ── ✅ P2 新增：断点续传辅助字段 ──────────
    resume_from_node_index: int | None
    """
    当用户从历史记录恢复执行时，指定从哪个节点索引继续。
    None 表示按当前 current_node_index 自然续传。
    """

    # ── ✅ P2 新增：面向前端的视图标记 ────────
    view_mode: Literal["full", "summary"] | None
    """
    控制节点详情面板的展示粒度。
    - "full": 展示所有字段（含 meta/debug）
    - "summary": 仅展示 sanitize_node_for_user 过滤后的字段
    默认 None 等同于 "summary"。
    """


def validate_user_action(action: str) -> UserAction:
    """
    将外部输入安全转换为合法 UserAction，非法值立即报错而非静默传播。

    Args:
        action: 来自前端/API 的用户操作字符串

    Returns:
        对应的 UserAction 枚举成员

    Raises:
        ValueError: 当 action 不是合法的 UserAction 值时
    """
    try:
        return UserAction(action)
    except ValueError:
        valid_values = [e.value for e in UserAction]
        raise ValueError(
            f"Invalid user action '{action}'. Expected one of {valid_values}"
        ) from None


