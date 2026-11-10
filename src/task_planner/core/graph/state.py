"""
state.py — LangGraph 状态定义
═══════════════════════════════════════════════════════════════════════
目标运行时：Python 3.14+（StrEnum / NotRequired / PEP 649 / PEP 695 全部原生）

── v8 ──
  P7-1：导入分组按 PEP 8 整理（stdlib / 第三方空行分隔）。
  P7-2：三指针优先级 docstring 补"消费方"说明——当前
           execute.py / route_after_execute 均未消费该字段，
           提醒下一轮迭代必须补齐实现方。

⚠️ 完整变更历史见 CHANGELOG.md。
"""
from enum import StrEnum
from typing import Annotated, Any, NotRequired, TypedDict, cast

from langgraph.graph import MessagesState


class UserAction(StrEnum):
    """用户对 interrupt 的响应动作。"""
    CONTINUE = "continue"
    CANCEL = "cancel"
    MODIFY = "modify"
    RETRY_NODE = "retry_node"


class NodeStatus(StrEnum):
    """
    单个节点的执行状态。公共契约，供执行器 / 前端状态栏使用。
    """
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class NodeResult(TypedDict):
    """
    node_results 列表里每个元素的形状。
    node_id 是 int | str 而非 int，因为 LLM 输出可能漂移。
    """
    node_id: int | str
    success: bool
    detail: str


def _replace_list[T](left: list[T] | None, right: list[T] | None) -> list[T]:
    """
    显式覆盖 reducer：总是用新值替换旧值。
    """
    if right is None:
        return []
    return right


class TaskState(MessagesState):
    """
    LangGraph 全局状态。继承 MessagesState 自带 messages 字段。

    执行指针优先级：
    retry_node_id > resume_from_node_index > current_node_index

    - retry_node_id：按 node["id"] 匹配（int/str 双兼容），不走 index 语义。
    - resume_from_node_index：从历史 checkpoint 恢复，只按 index 解释。
    - 两者皆空：按 current_node_index 自然续传。
    """

    # ── 用户输入（必填）──
    user_input: str
    thread_id: str

    # ── 意图识别 ──
    intent: dict[str, Any]
    needs_planning: bool

    # ── 规划结果 ──
    plan: dict[str, Any]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]

    # ── 经验检索（few-shot）──
    # retrieve_node 写入的相似案例，供 planner 做 few-shot。未启用时为空列表。
    retrieved_examples: NotRequired[list[dict[str, Any]]]


    # ── 数据库关联 ──
    task_id: str

    # ── 执行进度 ──
    current_node_index: int
    node_results: Annotated[list[NodeResult], _replace_list]

    # ── 渲染产物 ──
    # Cytoscape / PyVis 渲染后的 HTML 片段
    svg: NotRequired[str]
    flowchart_html: NotRequired[str]

    # ── 直接回答 ──
    direct_response: NotRequired[str]

    # ── 状态控制 ──
    # 人类可读的执行步骤日志，用于前端状态栏展示
    cancel_requested: bool
    error: NotRequired[str]
    status_text: str
    steps: Annotated[list[str], _replace_list]

    # ── 用户指令（interrupt / resume 用）──
    # 用户手动重试指定节点。仅按 node["id"] 匹配（int/str 双兼容），不走 index 语义。
    user_action: NotRequired[UserAction | None]
    modified_input: NotRequired[str | None]
    retry_node_id: NotRequired[int | str | None]

    # ── 断点续传辅助字段 ──
    # 当用户从历史记录恢复执行时，指定从哪个节点索引继续。
    resume_from_node_index: NotRequired[int | None]

    # ── Schema 版本（checkpoint 兼容性）──
    schema_version: int


def create_initial_state(
    user_input: str,
    thread_id: str,
    schema_version: int = 1,
    **kwargs: Any,
) -> TaskState:
    """
    构造带全部字段默认值的初始状态。
    """
    reserved = {"user_input", "thread_id", "schema_version"}
    conflict = reserved & kwargs.keys()
    if conflict:
        raise TypeError(
            f"create_initial_state() got reserved keys via kwargs: "
            f"{sorted(conflict)}; pass them as explicit arguments instead."
        )

    empty_node_results: list[NodeResult] = []

    state: dict[str, Any] = {
        "messages": [],
        "user_input": user_input,
        "thread_id": thread_id,
        "intent": {},
        "needs_planning": False,
        "plan": {},
        "nodes": [],
        "edges": [],
        "retrieved_examples": [],
        "task_id": "",
        "current_node_index": 0,
        "node_results": empty_node_results,
        "svg": "",
        "flowchart_html": "",
        "direct_response": "",
        "cancel_requested": False,
        "error": "",
        "status_text": "",
        "steps": [],
        "user_action": None,
        "modified_input": None,
        "retry_node_id": None,
        "resume_from_node_index": None,
        "schema_version": schema_version,
        **kwargs,
    }
    
    # 核心修复：使用 cast 告诉 Pyright，这是一个完美符合 TaskState 的字典
    return cast(TaskState, state)


def validate_user_action(action: str | UserAction) -> UserAction:
    """
    将外部输入安全转换为合法 UserAction，非法值立即报错。
    """
    try:
        return UserAction(action)
    except ValueError:
        valid_values = [e.value for e in UserAction]
        raise ValueError(
            f"Invalid user action '{action}'. Expected one of {valid_values}"
        ) from None


__all__ = [
    "UserAction",
    "NodeStatus",
    "NodeResult",
    "TaskState",
    "create_initial_state",
    "validate_user_action",
]
