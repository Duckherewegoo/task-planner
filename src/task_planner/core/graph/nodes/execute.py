"""execute.py — 单节点执行节点（含 interrupt + MODIFY 重置）"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from task_planner.core.db import mark_task_failed, mark_task_running, update_node_status
from task_planner.core.graph.state import TaskState, UserAction, validate_user_action
from task_planner.infrastructure.llm import LLMCancelledError
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.utils.context import get_cancel_event

from ._executor import execute_single_node
from .sanitize import sanitize_input, sanitize_node_for_user

logger = get_logger(__name__)

# DB 节点状态码（与 task_planner.core.db 的约定保持一致）
_NODE_STATUS_SUCCESS = 2
_NODE_STATUS_FAILED = 3


async def _safe_db_update(
    coro_factory: Callable[[], Awaitable[Any]],
    op_name: str,
    rid: str,
) -> None:
    """
    执行 DB 调用；失败只记 warning，不抛出。

    为什么需要这个包装：
      - save.py 在 DB 不可用时会降级，task_id 变成 "local-..." 前缀。
        后续 execute.py 里所有 DB 调用都会拿这个假 ID 去查 DB——
        DB 仍不可用 → 抛异常 → 若不吞掉，整个 execute_node 崩。
      - 这些 DB 调用是"尽力而为"的状态同步，不是关键路径。
        task_id 本身的真实性由 save.py 决定，execute.py 不因此改变行为。
      - 与 save.py 的宽捕获哲学一致：DB 问题降级，不阻断图。

    ⚠️ 宽捕获是有意的——任何 DB 异常都应触发降级。
       但如果 DB 层代码有 bug（TypeError / AttributeError 等），
       宽捕获会掩盖它。排查时留意 warning 里的 err 类型——
       非 IOError 类异常通常指向代码 bug 而非 DB 故障。

    Args:
        coro_factory: 返回协程的工厂（lambda），不是协程对象本身——
                      避免在异常路径创建出未 await 的协程。
        op_name:      操作名，用于日志。
        rid:          请求 ID。
    """
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
    按 state.py 声明的优先级裁决本轮执行的目标索引：
        retry_node_index > resume_from_node_index > current_node_index

    语义说明：
      - retry_node_index：workflow.py 的 `target_node_index` 参数写入，
        语义为**节点索引**。同时也兼容"按 node['id'] 匹配"场景——
        优先按 index 解释，越界后尝试按 id 查找，都失败则降级 current。
      - resume_from_node_index：断点续传起始索引，只按 index 解释。

    越界 / 类型非法时降级到 current_node_index 并 warn，不抛异常——
    坏指针不应把整个图卡死。
    """
    n = len(nodes)
    current_idx = state.get("current_node_index", 0)

    retry_id = state.get("retry_node_index")
    if retry_id is not None:
        # 首选 index 语义（与 workflow.py 的 target_node_index 命名一致）
        if isinstance(retry_id, int) and not isinstance(retry_id, bool) and 0 <= retry_id < n:
            return retry_id
        # 兼容 id 语义：在 nodes 里按 "id" 匹配
        for i, nd in enumerate(nodes):
            if isinstance(nd, dict) and nd.get("id") == retry_id:
                logger.info(
                    "[Graph] retry_node_index=%r 按 node['id'] 匹配到 index=%d (req=%s)",
                    retry_id, i, rid,
                )
                return i
        logger.warning(
            "[Graph] retry_node_index=%r 既不是有效 index 也不匹配任何 node['id']，"
            "降级 current_node_index=%d (req=%s)",
            retry_id, current_idx, rid,
        )
        return current_idx

    resume_idx = state.get("resume_from_node_index")
    if resume_idx is not None:
        if isinstance(resume_idx, int) and not isinstance(resume_idx, bool) and 0 <= resume_idx < n:
            return resume_idx
        logger.warning(
            "[Graph] resume_from_node_index=%r 越界 (0..%d)，"
            "降级 current_node_index=%d (req=%s)",
            resume_idx, n - 1, current_idx, rid,
        )
        return current_idx

    return current_idx


def _coerce_node_id(raw: Any, fallback: int) -> int | str:
    """
    与 _executor.py 的 _coerce_node_id 同策略——保证 NodeResult.node_id
    契约成立。非 int/str 时回落到 idx（合法 int）。
    """
    if isinstance(raw, (int, str)) and not isinstance(raw, bool):
        return raw
    return fallback


async def execute_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """执行节点（异步 + interrupt + 异常分级）"""
    rid = set_req_id()
    logger.info("[Graph] execute_node (req=%s)", rid)

    nodes = state["nodes"]

    # P0-3：三指针裁决——retry > resume > current
    idx = _resolve_target_index(state, nodes, rid)

    if idx >= len(nodes):
        logger.warning(
            "[Graph] execute_node: idx=%d 越界 (total=%d), 标记全部完成 (req=%s)",
            idx, len(nodes), rid,
        )
        return {
            "user_action": None,
            "status_text": "全部节点已完成",
            "steps": state.get("steps", []) + ["全部节点已完成"],
        }

    node = nodes[idx]
    nid = _coerce_node_id(node.get("id", idx), fallback=idx)
    name = node.get("name", f"步骤{nid}")

    # DB 调用经 _safe_db_update 包裹：
    # save.py 若降级（task_id="local-..."），此处 DB 调用会失败——
    # 但失败不应阻断执行。图的核心价值是"跑通节点"，
    # DB 状态同步是辅助功能。
    await _safe_db_update(
        lambda: mark_task_running(state["task_id"]),
        op_name="mark_task_running",
        rid=rid,
    )

    cancel_event = get_cancel_event()

    # ── 异常分级响应 ──
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
            "status_text": "⏹️ 已取消",
            "user_action": None,               # 显式清空，避免残留上一轮 action
            # P1：指针已被消费，取消路径同样需要清空
            "retry_node_index": None,
            "resume_from_node_index": None,
            "steps": state.get("steps", []) + [f"节点{nid} ⏹️ 用户取消"],
        }

    success = result.status == "success"
    detail = result.detail

    status_code = _NODE_STATUS_SUCCESS if success else _NODE_STATUS_FAILED
    # DB 调用经 _safe_db_update 包裹（原因同上）
    await _safe_db_update(
        lambda: update_node_status(state["task_id"], nid, status_code, details=detail),
        op_name=f"update_node_status(node={nid})",
        rid=rid,
    )

    node_results = state.get("node_results", []) + [
        {"node_id": nid, "success": success, "detail": detail}
    ]

    icon = "✅" if success else "❌"
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
        # P3：NodeExecResult 里 retryable 是必填字段，直接用下标
        "retryable": result.retryable,
    }

    feedback = interrupt(interrupt_payload)

    # ── 解析 interrupt 返回值 ──
    # P0-1/P0-2：键名必须与 state.py 字段名一致（workflow.py 的
    # resume_graph_async 也按同一约定写入 feedback）。
    action: UserAction
    modified_input: str | None

    if not isinstance(feedback, dict):
        logger.warning(
            "[Graph] execute_node: 非法 feedback 类型 %r，回退 CONTINUE (req=%s)",
            type(feedback).__name__, rid,
        )
        action = UserAction.CONTINUE
        modified_input = None
    else:
        raw_action = feedback.get("user_action", UserAction.CONTINUE)
        try:
            action = validate_user_action(raw_action)
        except ValueError:
            logger.warning(
                "[Graph] execute_node: 非法 user_action=%r，回退 CONTINUE (req=%s)",
                raw_action, rid,
            )
            action = UserAction.CONTINUE
        modified_input = feedback.get("modified_input", None)

    # ── 基础状态更新 ──
    # 三指针（retry / resume / current）语义：指针被消费后必须清空。
    updates: dict[str, Any] = {
        "node_results": node_results,
        "steps": steps,
        "user_action": action,
        "retry_node_index": None,
        "resume_from_node_index": None,
    }

    # RETRY_NODE 专用：不前进 index，让下一轮 execute_node
    # 重新执行同一节点。其他动作照常 +1。
    if action == UserAction.RETRY_NODE:
        updates["current_node_index"] = idx
        logger.info(
            "[Graph] execute_node: user_action=RETRY_NODE，"
            "保持 current_node_index=%d 重试 (req=%s)", idx, rid,
        )
    else:
        updates["current_node_index"] = idx + 1

    # ── MODIFY 分支：需 modified_input 非空才生效 ──
    if action == UserAction.MODIFY and modified_input:
        # 先把旧任务在 DB 里收尾，避免遗留 running 状态。
        # 经 _safe_db_update 包裹——DB 不可用时不影响重规划流程。
        if state.get("task_id"):
            await _safe_db_update(
                lambda: mark_task_failed(state["task_id"], error="用户修改需求，重新规划"),
                op_name=f"mark_task_failed(old task_id={state['task_id']})",
                rid=rid,
            )

        cleaned = sanitize_input(modified_input)
        updates.update({
            # 用户输入
            "user_input": cleaned,
            # 规划产物全部清空，重新开始
            "current_node_index": 0,
            "node_results": [],
            "nodes": [],
            "edges": [],
            "refined_nodes": [],
            "plan": {},
            "intent": {},
            # P0-4：与 create_initial_state 一致——默认走 intent，
            # 让 intent 节点重新判断。若设为 False，_route_start 会直接
            # 跳 direct_answer，用户改的需求不被识别就被回答。
            "needs_planning": True,
            "task_id": "",
            # 步骤日志重置（覆盖语义，直接给单元素列表）
            "steps": [
                f"需求已修改，重新规划: {cleaned[:50]}"
                + ("..." if len(cleaned) > 50 else "")
            ],
            # 渲染 / 回答产物清空
            "direct_response": "",
            "svg": "",
            "flowchart_html": "",
            # 状态控制
            "error": "",
            "status_text": "🔄 重新规划中",
            "cancel_requested": False,
            # 用户指令 / 断点续传指针全部清空
            # 与 state.py 声明的优先级 retry > resume > current 保持一致
            "user_action": None,
            "modified_input": None,
            "retry_node_index": None,
            "resume_from_node_index": None,
        })
    elif action == UserAction.MODIFY:
        # MODIFY 但无有效输入：降级为 CONTINUE，避免状态机进入矛盾态
        logger.warning(
            "[Graph] execute_node: MODIFY without modified_input，降级为 CONTINUE (req=%s)",
            rid,
        )
        updates["user_action"] = UserAction.CONTINUE

    return updates
