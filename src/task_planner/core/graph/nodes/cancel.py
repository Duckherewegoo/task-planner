"""cancel.py — 取消节点（善后，不负责打断）"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.db import mark_task_failed
from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


async def cancel_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """
    取消节点：任务取消后的善后处理。

    职责边界（两段式取消的一部分）：
      ① API 层 `set(cancel_event)` → 真正打断正在 await 的 LLM 调用。
         这一步**不在本节点**——本节点只负责"图到 END 之前的收尾"。
      ② 本节点：标记 DB 失败、写 status_text、清空用户指令与三指针。
      ③ 图路由到 END。

    ⚠️ 本节点不调用 `get_cancel_event().set()`。
       原因：能走到这里说明"打断"已经发生过（不管是被 API 层打断，
       还是用户从 checkpoint 恢复后主动选择取消）。重复 set 无意义，
       反而会污染 event 的语义。

    幂等性：
      - 若 `cancel_requested` 已为 True（比如 `execute.py` 的
        LLMCancelledError 分支先设过），跳过 DB 写入与 step 追加，
        避免重复 mark + 重复日志。
      - 无论幂等分支与否，返回值都保持 TaskState 的形状一致。

    Args:
        state:  全局状态
        config: LangGraph 节点签名约定保留，当前未使用。
                未来若要在此读 config 里的运行时对象（如 cancel_event），
                此参数已在位，不用改签名。
    """
    # ── 幂等保护 ──
    if state.get("cancel_requested"):
        logger.info("[Graph] cancel_node: 已取消过，跳过善后")
        return {}

    logger.info("[Graph] cancel_node")

    if state.get("task_id"):
        try:
            await mark_task_failed(state["task_id"], error="用户取消")
        except Exception as e:
            # 善后失败不应阻止图到 END——取消节点必须"绝对能完成"。
            # 用 error 级别让运维可见，但不重抛。
            logger.error("[Graph] cancel_node: mark_task_failed failed: %s", e)

    return {
        "cancel_requested": True,
        "status_text": "⏹️ 已取消",
        # ── 与 execute.py 的取消分支对齐：清空用户指令与三指针 ──
        # 取消后 state 会冻结在 checkpoint 里；若用户后续从该点 resume，
        # 残留的 user_action / 指针会导致路由异常。
        "user_action": None,
        "retry_node_index": None,
        "resume_from_node_index": None,
        "steps": state.get("steps", []) + ["任务已取消"],
    }
