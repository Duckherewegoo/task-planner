"""intent.py — 意图识别节点

契约：
  - 输入：state["user_input"]（已由上游 sanitize_input 脱敏过的版本）
  - 输出：更新 state["intent"] / state["needs_planning"] / state["steps"]
  - 本节点不做原始输入的二次脱敏——sanitize 责任在 API 层，
    本节点只负责在写回时**保持脱敏后的值**，不引入未脱敏内容。

⚠️ Pydantic 边界校验：
   本节点是 intent 数据的**唯一入口**。recognize_intent 的返回是
   LLM 输出——不可信。用 IntentModel 在边界处 model_validate，
   校验通过后 model_dump 落 state（消费方零改动）。

   schema 定义在 core/graph/schemas.py，与 infrastructure/prompts/intent.txt
   的 prompt 输出格式严格对齐——改 prompt 时同步改 schema。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from task_planner.core.graph.schemas import IntentModel
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
            "intent": dict,           # IntentModel.model_dump() 的结果
            "needs_planning": bool,
            "steps": list[str],       # 完整列表（覆盖语义）
        }

    ⚠️ 降级策略：
       LLM 输出若不符合 IntentModel schema（缺字段被默认值补上、
       非法枚举值、类型不匹配等），走 ValidationError 分支，降级为
       保守默认值 needs_planning=True（宁可多跑一次 plan，
       也不把复杂需求当寒暄处理掉）。
    """
    rid = set_req_id()
    logger.info("[Graph] intent_node (req=%s)", rid)

    user_input = sanitize_input(state["user_input"])
    cancel_event = get_cancel_event()
    raw_intent = await recognize_intent(
        user_input,
        req_id=rid,
        cancel_event=cancel_event,
    )

    # Pydantic 边界校验——LLM 输出不可信
    # - 缺字段 → 用模型默认值补上（category="other" / summary="" / ...）
    # - 多字段 → extra="ignore" 静默丢弃（LLM 多给不报错）
    # - 枚举非法（如 category="unknown_domain"）→ ValidationError
    # - 类型不匹配（如 needs_planning="not_a_bool"）→ ValidationError
    try:
        intent_model = IntentModel.model_validate(raw_intent)
    except ValidationError as e:
        logger.warning(
            "[Graph] intent 校验失败，降级为保守默认值 "
            "(needs_planning=True, category='other') | errors=%s (req=%s)",
            e.errors(),
            rid,
        )
        intent_model = IntentModel(
            needs_planning=True,
            category="other",
            summary="",
            complexity="simple",
        )

    # 落 state 的是纯 dict——消费方（plan / routes / refine / 其他）零改动。
    # 它们读 state["intent"]["category"] 依然是 str，不做模型反解。
    intent = intent_model.model_dump()

    return {
        # ⚠️ 覆盖 state["user_input"]：本字段在 state.py 契约中即"脱敏后版本"。
        # 原始输入不留存于 state，审计靠 sanitize_input 的 audit 日志（hash）。
        "user_input": user_input,
        "intent": intent,
        "needs_planning": intent_model.needs_planning,
        # 无 reducer 方案：手动拼完整列表（与 _replace_list 覆盖语义一致）
        "steps": state.get("steps", []) + [f"意图识别: planning={intent_model.needs_planning}"],
    }
