"""routes.py — 路由函数（纯计算，无 IO）

契约：
  - 所有函数输入为已由上游节点更新过的 TaskState
  - 所有函数不做 IO、不打日志
  - 正常路径不抛异常；防御性检查失败时抛 ValueError
  - 返回值必须与 workflow.py 的条件边映射表完全一致——
    用 Literal 类型让类型检查器验证

⚠️ user_action 契约与归一化：
  契约上 state["user_action"] 应是 UserAction 或 None——由 execute.py
  通过 validate_user_action 保证。但 checkpointer（PostgresSaver /
  SqliteSaver 等基于 msgpack 的外部存储）在序列化/反序列化时**不保留
  自定义类型**，会把 UserAction 退化成普通字符串，routes 层无法控制这一点。

  本文件用 validate_user_action 做一次归一化：
    - 合法 UserAction → 保持不变（isinstance 检查后跳过）
    - 合法字符串（如 "modify"）→ 转回 UserAction（checkpointer 场景）
    - 非法值（如 "foobar"）→ 抛 ValueError（快速失败，不静默走错分支）

  归一化的代价：仅在类型不匹配时的一次函数调用（幂等，微秒级）。
  收益：路由层对 checkpointer 的类型丢失免疫。

⚠️ 为什么不用 assert：
  1. `python -O` 会剥离所有 assert——生产若启用 -O 会静默失效。
  2. assert 定位是"开发者断言"（永远成立的事）；这里防的是"上游
     忘了校验"（运行时的输入契约），该用显式 raise。
  3. 不用 isinstance(action, (UserAction, str))——StrEnum 是 str 子类，
     这个元组等价于 str，会让任意字符串（如 "foobar"）通过，导致下面
     三个 if 全不命中、静默走 idx 分支——正是要防的"静默走错分支"。
"""
from typing import Literal

from task_planner.core.graph.state import TaskState, UserAction, validate_user_action

__all__ = [
    "route_after_intent",
    "route_after_execute",
    "route_after_render",
]


def route_after_intent(state: TaskState) -> Literal["cancel", "direct_answer", "plan"]:
    """
    意图节点之后的分流。

    优先级：
      1. cancel_requested=True → cancel
      2. needs_planning=False  → direct_answer
      3. 否则                  → plan

    needs_planning 的 fallback 是 True，与 state.py 的
    create_initial_state 默认值一致——初始状态永远走 plan 分支，
    由 intent 节点判断后回写。
    """
    if state.get("cancel_requested"):
        return "cancel"
    if not state.get("needs_planning", True):
        return "direct_answer"
    return "plan"


def route_after_execute(state: TaskState) -> Literal["cancel", "intent", "execute", "finish"]:
    """
    执行节点之后的分流。

    优先级：
      1. cancel_requested=True   → cancel
      2. user_action=CANCEL      → cancel
      3. user_action=MODIFY      → intent（回到意图识别重新规划）
      4. user_action=RETRY_NODE  → execute（重试）

         ⚠️ 依赖契约：execute.py 在 user_action=RETRY_NODE 时
         必须**不前进 current_node_index**，或显式写回 retry_node_index，
         否则下一轮 execute_node 会执行 idx+1 而非 idx——
         用户点"重试"会变成"继续"。
         （execute.py 已兑现：RETRY_NODE 分支保持 idx 不变，
          同时清空 retry_node_index，形成干净的"指针消费"闭环。）

      5. 无 user_action：按 current_node_index 与 nodes 长度比较
         - 已越界 → finish
         - 未越界 → execute

    Raises:
        ValueError: user_action 非 None 且不是合法的 UserAction 值
                    （由 validate_user_action 抛出）。这是防御性检查——
                    上游若漏掉校验，本函数快速失败而非静默走错分支。
    """
    if state.get("cancel_requested"):
        return "cancel"

    action = state.get("user_action")

    # 归一化：兼容 checkpointer 把 UserAction 反序列化成 str 的场景。
    # 契约路径下 action 已是 UserAction（execute.py 通过
    # validate_user_action 保证），isinstance 检查后跳过归一化，零开销。
    # 非契约路径（checkpointer 破坏类型）下会转回 UserAction。
    # 非法值由 validate_user_action 抛 ValueError——不静默走错分支。
    if action is not None and not isinstance(action, UserAction):
        action = validate_user_action(action)

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


def route_after_render(state: TaskState) -> Literal["execute"]:
    """
    渲染节点之后恒进入 execute。

    ⚠️ 恒返单值——workflow.py 里当前的写法是单目标条件边，等价于
       add_edge("render", "execute")。若要精简：
         1. workflow.py 里把条件边改成 add_edge
         2. 删除本函数及 __all__ 里的对应项
       两步必须同步，否则会有一个死函数或 import 错误。
    """
    return "execute"
