"""
state.py — LangGraph 状态定义
═══════════════════════════════════════════════════════════════════════
Changelog:
  ── v1 ──
  ✅ UserAction / NodeStatus 枚举
  ✅ TaskState 继承 MessagesState
  ✅ schema_version 字段（checkpoint 兼容）

  ── v2 ──
  ✅ P1-1：移除 steps / node_results 的 operator.add reducer。
           所有节点返回的是"完整列表"，与 reducer 语义冲突，
           会导致列表指数级重复。改为普通 list（覆盖语义），
           与节点实际行为一致。
  ✅ P1-2：删除未使用的 UserActionLiteral / NodeResult / validate_user_action。
  ✅ P1-3：清理 view_mode 的历史决策注释。
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from langgraph.graph import MessagesState


class UserAction(StrEnum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    MODIFY = "modify"
    RETRY_NODE = "retry_node"


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class TaskState(MessagesState):
    """
    LangGraph 全局状态。继承 MessagesState 自带 messages 字段。

    ⚠️ 注意：steps / node_results 使用覆盖语义（非 reducer）。
       所有节点约定返回"完整列表"，而非"增量"。
       如果你未来想改成 reducer 累积，必须同步把所有节点改成返回增量。
    """

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
    node_results: list[dict[str, Any]]

    # ── 渲染产物 ──
    svg: str
    flowchart_html: str

    # ── 直接回答 ──
    direct_response: str

    # ── 状态控制 ──
    cancel_requested: bool
    error: str
    status_text: str
    steps: list[str]

    # ── 用户指令（interrupt / resume 用）──
    user_action: UserAction | None
    modified_input: str | None
    retry_node_id: int | None

    # ── 断点续传辅助字段 ──
    resume_from_node_index: int | None

    # ── Schema 版本（checkpoint 兼容性）──
    schema_version: int


__all__ = ["UserAction", "NodeStatus", "TaskState"]
