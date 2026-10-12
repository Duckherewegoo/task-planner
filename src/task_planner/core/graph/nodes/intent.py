"""intent.py — 意图识别节点

契约：
  - 输入：state["user_input"]（已由上游 sanitize_input 脱敏过的版本）
  - 输出：更新 state["intent"] / state["needs_planning"] / state["steps"]
  - 本节点不做原始输入的二次脱敏——sanitize 责任在 API 层，
    本节点只负责在写回时**保持脱敏后的值**，不引入未脱敏内容。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import recognize_intent
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.utils.context import get_cancel_event

from .sanitize import sanitize_input

logger = get_logger(__name__)


async def intent_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """
    意图识别节点（异步）。

    Args:
        state:  全局状态。读 state["user_input"]（脱敏版）、state["steps"]。
        config: LangGraph 节点签名约定保留，当前透传给 get_cancel_event 读取
                运行时取消信号。未来若签名变化，此参数已在位。

    返回：
        {
            "user_input": str,        # 脱敏后的版本（覆盖写回，见 state.py 契约）
            "intent": dict,
            "needs_planning": bool,
            "steps": list[str],       # 完整列表（覆盖语义）
        }
    """
    rid = set_req_id()
    logger.info("[Graph] intent_node (req=%s)", rid)

    user_input = sanitize_input(state["user_input"])
    cancel_event = get_cancel_event()
    intent = await recognize_intent(user_input, req_id=rid, cancel_event=cancel_event)

    # ✅ 单点决策：默认值只在此计算一次，返回值与 steps 共用。
    # 默认 True（保守方向）——LLM 判断失败时宁可多跑一次 plan，
    # 也不要因为漏判把复杂需求当寒暄处理掉。
    # 与 state.py 的 create_initial_state 默认值一致。
    needs_planning = intent.get("needs_planning", True)

    return {
        # ⚠️ 覆盖 state["user_input"]：本字段在 state.py 契约中即"脱敏后版本"。
        #    原始输入不留存于 state，审计靠 sanitize_input 的 audit 日志（hash）。
        "user_input": user_input,
        "intent": intent,
        "needs_planning": needs_planning,
        # 无 reducer 方案：手动拼完整列表（与 _replace_list 覆盖语义一致）
        "steps": state.get("steps", []) + [
            f"意图识别: planning={needs_planning}"
        ],
    }
