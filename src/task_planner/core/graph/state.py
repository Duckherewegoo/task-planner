"""
state.py — LangGraph 状态定义
═══════════════════════════════════════════════════════════════════════
目标运行时：Python 3.14+（StrEnum / NotRequired / PEP 649 / PEP 695 全部原生）

Changelog:
  ── v1 ──
  ✅ UserAction / NodeStatus 枚举
  ✅ TaskState 继承 MessagesState
  ✅ schema_version 字段（checkpoint 兼容）

  ── v2 ──
  ✅ P1-1：移除 steps / node_results 的 operator.add reducer。
  ✅ P1-2：删除未使用的 UserActionLiteral / NodeResult 类型别名。
  ✅ P1-3：清理 view_mode 的历史决策注释。

  ── v3 ──
  ✅ P2-1：steps / node_results 用 Annotated[..., _replace_list] 显式声明覆盖语义。
  ✅ P2-2：所有非必填字段标 NotRequired。
  ✅ P2-3：集中声明三指针优先级。

  ── v4 ──
  ✅ P3-1：修正 v2 changelog 笔误——validate_user_action 并未删除。
  ✅ P3-2：_replace_list 对 None 做防御。
  ✅ P3-3：create_initial_state 显式拒绝 kwargs 覆盖必填字段。
  ✅ P3-4：NodeStatus 加 docstring。

  ── v5 ──
  ✅ P4-1：StrEnum / NotRequired 版本策略统一（当时假设支持 3.10）。
  ✅ P4-2：删除未使用的 TypedDict 导入。

  ── v6 ──
  ✅ P5-1：恢复 NodeResult TypedDict。
  ✅ P5-2：NodeResult.node_id 定为 int | str，与 _executor.py 契约对齐。
  ✅ P5-3：_replace_list 泛型化。
  ✅ P5-4：validate_user_action 参数放宽为 str | UserAction。
  ✅ P5-5：create_initial_state 内 node_results 显式标注。

  ── v7 ──
  ✅ P6-1：明确目标 Python 版本为 3.14。撤销 v5 的 try/except 回退。
  ✅ P6-2：移除 `from __future__ import annotations`。
  ✅ P6-3：_replace_list 改用 PEP 695 内联泛型语法。
  ✅ P6-4：模块顶部显式声明"目标运行时"。

  ── v8 ──
  ✅ Patch A：needs_planning 默认值改为 True。
              与 workflow.py 的 _route_start 契约对齐——新任务默认走
              intent 由意图节点判断，避免 create_initial_state 后
              直接跳 direct_answer、intent 永不执行。
  ✅ P7-1：导入分组按 PEP 8 整理（stdlib / 第三方空行分隔）。
  ✅ P7-2：三指针优先级 docstring 补"消费方"说明——当前
           execute.py / route_after_execute 均未消费该字段，
           提醒下一轮迭代必须补齐实现方。
"""
from enum import StrEnum
from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.graph import MessagesState


class UserAction(StrEnum):
    """用户对 interrupt 的响应动作。"""
    CONTINUE = "continue"
    CANCEL = "cancel"
    MODIFY = "modify"
    RETRY_NODE = "retry_node"


class NodeStatus(StrEnum):
    """
    单个节点的执行状态。公共契约，供执行器 / 前端状态栏使用，
    本文件不直接引用。
    """
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class NodeResult(TypedDict):
    """
    node_results 列表里每个元素的形状。

    ⚠️ node_id 是 int | str 而非 int：
       _executor.py 里 node["id"] 来自 LLM 输出，可能缺失/漂移，
       _coerce_node_id 会把非 int/str 降级为 str（如 "?"）。
       下游按 int 消费会炸，这里必须与执行器契约一致。
    """
    node_id: int | str
    success: bool
    detail: str


def _replace_list[T](left: list[T] | None, right: list[T] | None) -> list[T]:
    """
    显式覆盖 reducer：总是用新值替换旧值。

    PEP 695 内联泛型——同时服务 list[str]（steps）与 list[NodeResult]
    （node_results），元素类型不再被抹平。

    语义：
      - 节点返回 []，结果就是 []
      - 节点返回 [x]，结果就是 [x]（覆盖）
      - 节点返回 None，视为"清空"，结果为 []（防御性处理）
      - 想要累积，调用方自己把旧列表拼上再返回

    ⚠️ 局限：本 reducer 只保证"覆盖语义"，不保证"节点真的返回完整列表"。
       若节点返回增量，历史仍会丢——这是灵活性换防呆性的取舍。
       如需强防呆，另供 append_step() / append_node_result() helper，
       节点只准走 helper。
    """
    if right is None:
        return []
    return right


class TaskState(MessagesState):
    """
    LangGraph 全局状态。继承 MessagesState 自带 messages 字段。

    ⚠️ 覆盖语义说明：
       steps / node_results 使用覆盖语义（非累积）。
       已通过 Annotated[..., _replace_list] 显式声明，机制化保证。
       所有节点必须返回"完整列表"，而非"增量"。

    ⚠️ 执行指针优先级：
       retry_node_id > resume_from_node_index > current_node_index
       - retry_node_id 非空：用户手动重试某节点，直接跳转该节点。
       - resume_from_node_index 非空：从历史 checkpoint 恢复，从该索引继续。
       - 两者皆空：按 current_node_index 自然续传。
       三个字段不应同时被不同来源写入；执行器入口负责裁决。
       ⚠️ 此优先级在此处声明，实现必须在 executor 里与之保持一致——
          文档与实现不一致比没有文档更危险。
       ⚠️ 当前状态：execute.py 只读取 current_node_index，
          retry_node_id / resume_from_node_index 从未被消费——
          本优先级声明尚未在任何实现层兑现。下一轮迭代必须在
          execute.py 或 route_after_execute 中补齐裁决逻辑。

    ⚠️ 初始化建议：
       所有 NotRequired 字段在 graph invoke 时若不传，节点访问会 KeyError。
       NotRequired 只在类型层声明"可以不填"，运行时不会补默认值。
       请统一使用 create_initial_state() 生成初始状态。
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
    retrieved_examples: NotRequired[list[dict[str, Any]]]
    """retrieve_node 写入的相似案例，供 planner 做 few-shot。未启用时为空列表。
       ⚠️ 本字段在 retrieve 模块未接入主图前始终为 []。"""

    # ── 节点细化 ──
    refined_nodes: list[dict[str, Any]]

    # ── 数据库关联 ──
    task_id: str

    # ── 执行进度 ──
    current_node_index: int
    node_results: Annotated[list[NodeResult], _replace_list]

    # ── 渲染产物 ──
    svg: NotRequired[str]
    flowchart_html: NotRequired[str]
    """Cytoscape / PyVis 渲染后的 HTML 片段"""

    # ── 直接回答 ──
    direct_response: NotRequired[str]

    # ── 状态控制 ──
    cancel_requested: bool
    error: NotRequired[str]
    status_text: str
    steps: Annotated[list[str], _replace_list]
    """人类可读的执行步骤日志，用于前端状态栏展示"""

    # ── 用户指令（interrupt / resume 用）──
    user_action: NotRequired[UserAction | None]
    modified_input: NotRequired[str | None]
    retry_node_id: NotRequired[int | None]

    # ── 断点续传辅助字段 ──
    resume_from_node_index: NotRequired[int | None]
    """
    当用户从历史记录恢复执行时，指定从哪个节点索引继续。
    None 表示按当前 current_node_index 自然续传。
    优先级：retry_node_id > resume_from_node_index > current_node_index。
    """

    # ── Schema 版本（checkpoint 兼容性）──
    schema_version: int


def create_initial_state(
    user_input: str,
    thread_id: str,
    schema_version: int = 1,
    **kwargs: Any,
) -> TaskState:
    """
    构造带全部字段默认值的初始状态，避免节点访问 NotRequired 字段时 KeyError。
    调用方可传入额外字段覆盖默认值。

    Raises:
        TypeError: kwargs 里出现 user_input / thread_id / schema_version，
                   这三个字段必须走显式位置/关键字参数，避免静默覆盖。
    """
    reserved = {"user_input", "thread_id", "schema_version"}
    conflict = reserved & kwargs.keys()
    if conflict:
        raise TypeError(
            f"create_initial_state() got reserved keys via kwargs: "
            f"{sorted(conflict)}; pass them as explicit arguments instead."
        )

    # node_results 显式标注，避免 [] 被类型推断为 list[Never]
    empty_node_results: list[NodeResult] = []

    state: TaskState = {
        "user_input": user_input,
        "thread_id": thread_id,
        "intent": {},
        # ✅ Patch A：默认走 intent，由 intent 节点判断后回写。
        # 与 workflow.py 的 _route_start 契约一致——
        # 若此处为 False，START 会直接跳 direct_answer，intent 永不执行。
        "needs_planning": True,
        "plan": {},
        "nodes": [],
        "edges": [],
        "retrieved_examples": [],
        "refined_nodes": [],
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
    return state


def validate_user_action(action: str | UserAction) -> UserAction:
    """
    将外部输入安全转换为合法 UserAction，非法值立即报错而非静默传播。

    Args:
        action: 来自前端/API 的用户操作字符串，或已经是 UserAction 枚举。
                execute.py 里 raw_action 来自 interrupt 返回值，可能是两者之一。

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


__all__ = [
    "UserAction",
    "NodeStatus",
    "NodeResult",
    "TaskState",
    "create_initial_state",
    "validate_user_action",
]
