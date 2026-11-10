"""intent.py — 意图识别节点

契约：
  - 输入：state["user_input"]（可能是脱敏版，也可能是旧 Checkpoint 的原始版）
  - 输出：更新 state["intent"] / state["needs_planning"] / state["steps"]，
          并且**强制将脱敏后的安全版本覆盖写回** state["user_input"]。

⚠️ 安全防线（关于脱敏）：
   尽管 API 层在入口处已做过一次脱敏，但 State 的数据来源不可控（如
   历史 Checkpoint 恢复、内部服务直调）。本节点作为调用 LLM 前的
   最后一道防线，必须在此处再次调用 sanitize_input。
   
   这是典型的防御性编程（Defensive Programming）：
   1. sanitize_input 是幂等的，重复调用无副作用。
   2. 覆盖写回 state["user_input"]，确保一旦流经本节点，State 中
      绝不再留存任何原始未脱敏的敏感信息。

⚠️ Pydantic 边界校验：
   本节点是 intent 数据的**唯一入口**。recognize_intent 的返回是
   LLM 输出——不可信。用 IntentModel 在边界处 model_validate，
   校验通过后 model_dump 落 state（消费方零改动）。
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
        config: LangGraph 节点签名约定保留。当前本节点未消费该参数。
                取消信号通过 utils.context.get_cancel_event() 从 contextvars 
                中获取，与 config 无关。

    返回：
        {
            "user_input": str,        # 脱敏后的版本（覆盖写回）
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
    # 从 config 中提取 thread_id，增强日志上下文
    thread_id = config.get("configurable", {}).get("thread_id", "unknown")
    logger.info("[Graph] intent_node (req=%s, thread=%s)", rid, thread_id)

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
        # ⚠️ 强制覆盖 state["user_input"]：
        # 这是最后一道脱敏防线。即使上游漏脱、或从旧 Checkpoint 恢复，
        # 只要进了本节点，后续所有节点读到的绝对是安全版本。
        "user_input": user_input,
        "intent": intent,
        "needs_planning": intent_model.needs_planning,
        # 无 reducer 方案：手动拼完整列表（与 _replace_list 覆盖语义一致）
        "steps": state.get("steps", []) + [f"意图识别: planning={intent_model.needs_planning}"],
    }
