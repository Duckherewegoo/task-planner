"""
state.py — LangGraph 状态定义
═══════════════════════════════════════════════════════════════════════
目标运行时：Python 3.14+（StrEnum / NotRequired / PEP 649 / PEP 695 全部原生）

── v8 ──
  Patch A：needs_planning 默认值改为 True。
              与 workflow.py 的 _route_start 契约对齐——新任务默认走
              intent 由意图节点判断，避免 create_initial_state 后
              直接跳 direct_answer、intent 永不执行。
  P7-1：导入分组按 PEP 8 整理（stdlib / 第三方空行分隔）。
  P7-2：三指针优先级 docstring 补"消费方"说明——当前
           execute.py / route_after_execute 均未消费该字段，
           提醒下一轮迭代必须补齐实现方。

⚠️ 完整变更历史见 CHANGELOG.md。
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
       retry_node_index > resume_from_node_index > current_node_index
       - retry_node_index 非空：用户手动重试某节点，直接跳转该节点。
       - resume_from_node_index 非空：从历史 checkpoint 恢复，从该索引继续。
       - 两者皆空：按 current_node_index 自然续传。
       三个字段不应同时被不同来源写入；执行器入口负责裁决。
       ⚠️ 此优先级在此处声明，实现必须在 executor 里与之保持一致——
          文档与实现不一致比没有文档更危险。
       ⚠️ 当前状态：execute.py 只读取 current_node_index，
          retry_node_index / resume_from_node_index 从未被消费——
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
    retry_node_index: NotRequired[int | None]

    # ── 断点续传辅助字段 ──
    resume_from_node_index: NotRequired[int | None]
    """
    当用户从历史记录恢复执行时，指定从哪个节点索引继续。
    None 表示按当前 current_node_index 自然续传。
    优先级：retry_node_index > resume_from_node_index > current_node_index。
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

    ⚠️ 下一层应"组合"而非"复制"：
       应用层（如 services/agent/state.py 的 make_initial_state）需要叠加
       业务判断（跳过规划、强制意图等）时，**应调用本函数并传 override**，
       而不是自己重新写一份字段清单。

       反例（禁止）：
           def make_initial_state(...):
               return {
                   "messages": [],           # ← 复制字段清单
                   "user_input": ...,
                   # ... 23 个字段
               }
       正例（推荐）：
           def make_initial_state(...):
               skip = should_skip_planning(...)
               return create_initial_state(
                   user_input, thread_id,
                   needs_planning=not skip,
                   direct_response=...,
                   steps=[...],
               )

       理由：字段清单只能有一处真相源。复制清单会让"新增字段"变成
       "改 N 个地方"，且未改的地方会因 NotRequired 静默缺字段——
       下游 KeyError 时不知道是谁漏的。

    Args:
        user_input:  用户原始输入
        thread_id:   会话 ID
        schema_version: checkpoint 兼容性版本号
        **kwargs:    字段覆盖。不能包含 user_input / thread_id /
                     schema_version（这三个是显式参数，通过 kwargs
                     传入会抛 TypeError，避免静默覆盖）。

    Returns:
        TaskState：字段完整（含 messages）、默认值已填。

    Raises:
        TypeError: kwargs 里出现保留字段名。
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
        # 显式初始化 messages——MessagesState 的 add_messages reducer
        # 对 [] 是 no-op，显式给 [] 保证字段完整性（下游按字段存在性
        # 做契约检查时不会误判）。
        "messages": [],
        "user_input": user_input,
        "thread_id": thread_id,
        "intent": {},
        "needs_planning": False,
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
        "retry_node_index": None,
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
