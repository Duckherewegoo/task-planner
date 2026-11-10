"""execute.py — 单节点执行节点（含 interrupt + MODIFY 重置）"""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from task_planner.core.db import mark_task_failed, mark_task_running, update_node_status
from task_planner.core.graph.state import TaskState, UserAction, validate_user_action
from task_planner.infrastructure.llm import LLMCancelledError
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.utils.context import get_cancel_event

from ._executor import execute_single_node
from ._helpers import coerce_node_id as _coerce_node_id
from ._helpers import coerce_pointer as _coerce_pointer
from ._helpers import ids_equal as _ids_equal
from .sanitize import sanitize_input, sanitize_node_for_user

logger = get_logger(__name__)

_NODE_STATUS_SUCCESS = 2
_NODE_STATUS_FAILED = 3


async def _safe_db_update(
    coro_factory: Callable[[], Awaitable[Any]],
    op_name: str,
    rid: str,
) -> None:
    """执行 DB 调用；失败只记 warning，不抛出。"""
    try:
        await coro_factory()
    except Exception as e:
        logger.warning("[Graph] %s 失败(不影响执行): %s (req=%s)", op_name, e, rid)


def _resolve_target_index(
    state: TaskState,
    nodes: list[dict[str, Any]],
    rid: str,
) -> int:
    """
    按 state.py 声明的优先级裁决本轮执行的目标索引。
    优先级：retry_node_id > resume_from_node_index > current_node_index

    - retry_node_id：只按 node["id"] 匹配（int/str 双向等价）。
    - resume_from_node_index：只按 index 解释，越界降级 current。
    """
    n = len(nodes)
    current_idx = state.get("current_node_index", 0)

    # ── 第一优先级：retry_node_id（只走 ID 语义）──
    retry_id = _coerce_pointer(state.get("retry_node_id"))
    if retry_id is not None:
        for i, nd in enumerate(nodes):
            if _ids_equal(nd.get("id"), retry_id):
                logger.info(
                    "[Graph] retry_node_id=%r 匹配到 index=%d (req=%s)",
                    retry_id,
                    i,
                    rid,
                )
                return i
        logger.warning(
            "[Graph] retry_node_id=%r 未匹配到任何 node['id']，降级 current_node_index=%d (req=%s)",
            retry_id,
            current_idx,
            rid,
        )
        return current_idx

    # ── 第二优先级：resume_from_node_index（只走 Index 语义）──
    resume_idx = _coerce_pointer(state.get("resume_from_node_index"))
    if resume_idx is not None:
        if isinstance(resume_idx, int) and 0 <= resume_idx < n:
            return resume_idx
        logger.warning(
            "[Graph] resume_from_node_index=%r 越界或非法 (0..%d)，降级 current_node_index=%d (req=%s)",
            resume_idx,
            n - 1,
            current_idx,
            rid,
        )
        return current_idx

    return current_idx


async def execute_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """执行节点（异步 + interrupt + 异常分级）"""
    rid = set_req_id()
    logger.info("[Graph] execute_node (req=%s)", rid)

    nodes = state["nodes"]

    idx = _resolve_target_index(state, nodes, rid)

    if idx >= len(nodes):
        logger.warning(
            "[Graph] execute_node: idx=%d 越界 (total=%d), 标记全部完成 (req=%s)",
            idx,
            len(nodes),
            rid,
        )
        return {
            "user_action": None,
            "status_text": "全部节点已完成",
            "steps": state.get("steps", []) + ["全部节点已完成"],
        }

    node = nodes[idx]
    nid = _coerce_node_id(node.get("id", idx), fallback=idx)
    name = node.get("name", f"步骤{nid}")

    await _safe_db_update(
        lambda: mark_task_running(state["task_id"]),
        op_name="mark_task_running",
        rid=rid,
    )

    cancel_event = get_cancel_event()

    try:
        result = await execute_single_node(
            node=node,
            user_input=state["user_input"],
            intent=state["intent"],
            cancel_event=cancel_event,
            req_id=rid,
        )
    except LLMCancelledError:
        logger.info("[Graph] execute_node: 取消信号，跳过 interrupt (req=%s)", rid)
        return {
            "cancel_requested": True,
            "status_text": "[已取消]",
            "user_action": None,
            "retry_node_id": None,
            "resume_from_node_index": None,
            "steps": state.get("steps", []) + [f"节点{nid} [用户取消]"],
        }

    success = result.status == "success"
    detail = result.detail

    if isinstance(nid, int):
        status_code = _NODE_STATUS_SUCCESS if success else _NODE_STATUS_FAILED
        await _safe_db_update(
            lambda: update_node_status(state["task_id"], nid, status_code, details=detail),
            op_name=f"update_node_status(node={nid})",
            rid=rid,
        )

    node_results = state.get("node_results", []) + [
        {"node_id": nid, "success": success, "detail": detail}
    ]

    icon = "[成功]" if success else "[失败]"
    steps = state.get("steps", []) + [f"节点{nid} {icon} {name}"]

    user_visible_node = sanitize_node_for_user(node)
    interrupt_payload = {
        "type": "node_complete",
        "node_id": nid,
        "node_name": name,
        "success": success,
        "detail": detail,
        "node_info": user_visible_node,
        "next_index": idx + 1,
        "total_nodes": len(nodes),
        "retryable": result.retryable,
    }

    feedback: Any = interrupt(interrupt_payload)

    action: UserAction
    modified_input: str | None

    if not isinstance(feedback, dict):
        logger.warning(
            "[Graph] execute_node: 非法 feedback 类型 %r，回退 CONTINUE (req=%s)",
            type(feedback).__name__,
            rid,
        )
        action = UserAction.CONTINUE
        modified_input = None
    else:
        raw_action: Any = feedback.get("user_action", UserAction.CONTINUE)
        try:
            action = validate_user_action(raw_action)
        except ValueError:
            logger.warning(
                "[Graph] execute_node: 非法 user_action=%r，回退 CONTINUE (req=%s)",
                raw_action,
                rid,
            )
            action = UserAction.CONTINUE
        modified_input = feedback.get("modified_input", None)

    updates: dict[str, Any] = {
        "node_results": node_results,
        "steps": steps,
        "user_action": action,
        "retry_node_id": None,
        "resume_from_node_index": None,
    }

    if action == UserAction.RETRY_NODE:
        updates["current_node_index"] = idx
        logger.info(
            "[Graph] execute_node: user_action=RETRY_NODE，"
            "保持 current_node_index=%d 重试 (req=%s)",
            idx,
            rid,
        )
    else:
        updates["current_node_index"] = idx + 1

    if action == UserAction.MODIFY and modified_input:
        if state.get("task_id"):
            await _safe_db_update(
                lambda: mark_task_failed(state["task_id"], error="用户修改需求，重新规划"),
                op_name=f"mark_task_failed(old task_id={state['task_id']})",
                rid=rid,
            )

        cleaned: str = sanitize_input(modified_input)
        updates.update(
            {
                "user_input": cleaned,
                "current_node_index": 0,
                "node_results": [],
                "nodes": [],
                "edges": [],
                "plan": {},
                "intent": {},
                "needs_planning": True,
                "task_id": "",
                "steps": [
                    f"需求已修改，重新规划: {cleaned[:50]}" + ("..." if len(cleaned) > 50 else "")
                ],
                "direct_response": "",
                "svg": "",
                "flowchart_html": "",
                "error": "",
                "status_text": "[重新规划中]",
                "cancel_requested": False,
                "user_action": None,
                "modified_input": None,
                "retry_node_id": None,
                "resume_from_node_index": None,
            }
        )
    elif action == UserAction.MODIFY:
        logger.warning(
            "[Graph] execute_node: MODIFY without modified_input，降级为 CONTINUE (req=%s)",
            rid,
        )
        updates["user_action"] = UserAction.CONTINUE

    return updates
