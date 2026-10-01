"""
nodes.py — LangGraph 异步节点函数（完全异步化）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Optional, TypedDict, cast

from langchain_core.runnables import RunnableConfig

from task_planner.core.database import (
    create_task_with_plan,
    create_direct_answer_task,
    mark_task_running,
    mark_task_failed,
    update_node_status,
)
from task_planner.infrastructure.llm_client import (
    direct_chat,
    generate_plan,
    recognize_intent,
    refine_node,
    # ✅ 修复 P0-1：补上缺失的三个符号
    execute_node_llm,
    LLMCancelledError,
    LLMTimeoutError,
    _render_template,
    _extract_json as _parse_json,
)
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.utils.context import cancel_event_var
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import USER_VISIBLE_NODE_FIELDS
from task_planner.infrastructure.regexes import SENSITIVE_PATTERNS
from task_planner.infrastructure.prompts.loader import EXECUTE_NODE_PROMPT
LLM_NODE_MODEL = _hub.dev.LLM_NODE_MODEL
LLM_NODE_TIMEOUT = _hub.dev.LLM_NODE_TIMEOUT
MAX_INPUT_LENGTH = _hub.dev.MAX_INPUT_LENGTH
from task_planner.core.graph.state import TaskState, UserAction

logger = get_logger(__name__)
_SENSITIVE_PATTERNS = SENSITIVE_PATTERNS


class SafeNodeView(TypedDict, total=False):
    """面向前端的节点视图"""
    id: int
    name: str
    details: str
    status: str
    result: str


def sanitize_node_for_user(node: dict[str, Any]) -> SafeNodeView:
    """过滤掉内部 debug/meta 字段"""
    # ✅ 修复 P1：用 cast 替代 type: ignore
    return cast(
        SafeNodeView,
        {k: v for k, v in node.items() if k in USER_VISIBLE_NODE_FIELDS},
    )


def sanitize_nodes_for_user(nodes: list[dict[str, Any]]) -> list[SafeNodeView]:
    return [sanitize_node_for_user(n) for n in nodes]


# ══════════════════════════════════════════════════
#  输入清理（同步）
# ══════════════════════════════════════════════════
def _sanitize_input(text: str, *, audit_log: bool = True) -> str:
    original_hash = hashlib.sha256(text.encode()).hexdigest() if audit_log else None
    sanitized = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    if audit_log and sanitized != text:
        logger.info(
            "[Audit] Input redacted | hash=%s | sanitized_len=%d",
            original_hash,
            len(sanitized),
        )
    return sanitized


# ══════════════════════════════════════════════════
#  获取取消事件（从 ContextVar）
# ══════════════════════════════════════════════════
def _get_cancel_event(config: RunnableConfig | None = None) -> asyncio.Event | None:
    """
    从 ContextVar 获取 asyncio.Event（如不存在则返回 None）。

    config 参数保留以兼容 LangGraph 节点签名，以及未来可能的扩展
    （例如从 config 直接取 cancel 信号）。
    """
    return cancel_event_var.get()


# ══════════════════════════════════════════════════
#  异步节点函数
# ══════════════════════════════════════════════════

async def intent_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """意图识别节点（异步）"""
    rid = set_req_id()
    logger.info("[Graph] intent_node (req=%s)", rid)

    user_input = _sanitize_input(state["user_input"])
    cancel_event = _get_cancel_event(config)
    intent = await recognize_intent(user_input, req_id=rid, cancel_event=cancel_event)

    return {
        "user_input": user_input,
        "intent": intent,
        "needs_planning": intent.get("needs_planning", False),
        # ✅ 注：无 reducer 方案下，这里是「读旧 + 追加」的手动累积，保持原样
        "steps": state.get("steps", []) + [f"意图识别: planning={intent.get('needs_planning')}"],
    }


async def plan_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """规划节点（异步）"""
    rid = set_req_id()
    logger.info("[Graph] plan_node (req=%s)", rid)

    cancel_event = _get_cancel_event(config)
    plan = await generate_plan(
        state["user_input"], state["intent"],
        req_id=rid, cancel_event=cancel_event,
    )

    nodes_raw = list(plan.get("nodes", []))
    edges_raw = list(plan.get("edges", []))

    if not nodes_raw:
        logger.error(
            "[Graph] plan_node 产出空计划! raw_plan=%s (req=%s)",
            str(plan)[:500], rid,
        )
        raise ValueError(
            f"规划失败：LLM 未返回有效节点。请检查 planner prompt 或模型输出格式。(req={rid})"
        )

    logger.info(
        "[Graph] plan_node: %d nodes, %d edges (req=%s)",
        len(nodes_raw), len(edges_raw), rid,
    )

    return {
        "plan": plan,
        "nodes": nodes_raw,
        "edges": edges_raw,
        "steps": state.get("steps", []) + [f"规划完成: {len(nodes_raw)}个节点, {len(edges_raw)}条边"],
    }


async def refine_node_fn(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """节点细化（异步并发）"""
    rid = set_req_id()
    logger.info("[Graph] refine_node (req=%s)", rid)

    nodes_raw = state["nodes"]
    intent = state["intent"]
    cancel_event = _get_cancel_event(config)

    async def _refine_one(n: dict[str, Any]) -> dict[str, Any]:
        if cancel_event and cancel_event.is_set():
            logger.info("[Graph] 节点%s细化跳过(已取消, req=%s)", n.get("id"), rid)
            n["details"] = "⏹️ 用户取消"
            return n
        try:
            r = await refine_node(
                n,
                state["user_input"],
                str(intent.get("category", "other")),
                req_id=rid,
                cancel_event=cancel_event,
            )
            n["name"] = r.get("name", n.get("name", ""))
            n["details"] = r.get("details", r.get("detail", ""))
            n["meta"] = r.get("meta", {})
        except (RuntimeError, TimeoutError, asyncio.CancelledError) as e:
            logger.warning("[Graph] 节点%s细化失败: %s (req=%s)", n.get("id"), e, rid)
            n["details"] = f"细化失败: {e}"
        return n

    tasks = []
    for n in nodes_raw:
        if cancel_event and cancel_event.is_set():
            logger.info(
                "[Graph] refine 提前终止，已提交 %d/%d (req=%s)",
                len(tasks), len(nodes_raw), rid,
            )
            break
        tasks.append(_refine_one(dict(n)))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    refined = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            fallback = dict(nodes_raw[i])
            fallback["details"] = f"细化异常: {res}"
            refined.append(fallback)
        else:
            refined.append(res)

    if cancel_event and cancel_event.is_set():
        for i in range(len(refined), len(nodes_raw)):
            fallback = dict(nodes_raw[i])
            fallback["details"] = "⏹️ 用户取消(未提交)"
            refined.append(fallback)

    return {
        "refined_nodes": refined,
        "nodes": refined,
        "steps": state.get("steps", []) + ["节点细化完成"],
    }


async def save_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """
    入库节点（异步）。

    ✅ 降级策略：DB 不可用时不再让整个图崩溃，而是：
       - 生成一个本地 task_id（uuid 前缀标记）
       - 记录 warning 日志
       - 继续后续节点（render / execute）

    这样评估、CI、本地 demo 无需 MongoDB 也能跑通。
    """
    import uuid

    rid = set_req_id()
    logger.info("[Graph] save_node (req=%s)", rid)

    try:
        task_doc, plan_doc = await create_task_with_plan(
            raw_query=state["user_input"],
            intent_info=state["intent"],
            plan_data={"nodes": state["nodes"], "edges": state["edges"]},
            req_id=rid,
        )
        task_id = task_doc["task_id"]
        logger.info("[Graph] save_node 入库成功: %s", task_id)
        return {
            "task_id": task_id,
            "steps": state.get("steps", []) + [f"已入库: {task_id}"],
        }

    except Exception as e:
        # ✅ 降级：DB 不可用时用本地 ID 继续
        fallback_id = f"local-{uuid.uuid4().hex[:12]}"
        logger.warning(
            "[Graph] save_node DB 写入失败，降级为本地 ID: %s | err=%s",
            fallback_id, e,
        )
        return {
            "task_id": fallback_id,
            "steps": state.get("steps", []) + [f"⚠️ 未入库(降级): {fallback_id}"],
            "error": "",   # 不污染 error 字段，图继续执行
        }

async def render_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """渲染流程图（CPU 密集，仍用 to_thread）"""
    rid = set_req_id()
    logger.info("[Graph] render_node (req=%s)", rid)

    from task_planner.utils.flowchart_pro import flowchart_pro

    html = await asyncio.to_thread(
        flowchart_pro.render_interactive,
        state["nodes"],
        state["edges"],
        state["task_id"],
        rid,
    )

    return {
        "flowchart_html": html,
        "steps": state.get("steps", []) + ["流程图渲染完成"],
    }


async def execute_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    # ✅ 修复 P1：返回类型统一为 dict[str, Any]
    """执行节点（异步 + interrupt + 异常分级响应）"""
    rid = set_req_id()
    logger.info("[Graph] execute_node (req=%s)", rid)

    from langgraph.types import interrupt

    nodes = state["nodes"]
    idx = state.get("current_node_index", 0)

    if idx >= len(nodes):
        return {"user_action": None}

    node = nodes[idx]
    nid = node.get("id", idx)
    name = node.get("name", f"步骤{nid}")

    await mark_task_running(state["task_id"])

    cancel_event = _get_cancel_event(config)

    # ── 异常分级响应 ──────────────────────────────────────────────
    try:
        result = await _execute_single_node(
            node=node,
            user_input=state["user_input"],
            intent=state["intent"],
            cancel_event=cancel_event,
            req_id=rid,
        )
    except LLMCancelledError:
        # 🛑 用户主动取消 → 不写数据库、不触发 interrupt、直接返回取消状态
        logger.info("[Graph] execute_node: 取消信号，跳过 interrupt (req=%s)", rid)
        return {
            "cancel_requested": True,
            "status_text": "⏹️ 已取消",
            "steps": state.get("steps", []) + [f"节点{nid} ⏹️ 用户取消"],
        }

    # ── 从结构化结果中提取字段 ────────────────────────────────────
    success = result["status"] == "success"
    detail = result["detail"]

    status_code = 2 if success else 3
    await update_node_status(state["task_id"], nid, status_code, details=detail)

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
        "retryable": result.get("retryable", False),
    }

    feedback = interrupt(interrupt_payload)

    action = feedback.get("action", UserAction.CONTINUE)
    modified_input = feedback.get("user_input", None)

    updates: dict[str, Any] = {
        "current_node_index": idx + 1,
        "node_results": node_results,
        "steps": steps,
        "user_action": action,
    }

    if action == UserAction.MODIFY and modified_input:
        # ✅ 修复 P0-3：MODIFY 分支重置所有"上一轮残留"状态，避免 steps 里旧日志污染前端
        cleaned = _sanitize_input(modified_input)
        updates["user_input"] = cleaned
        updates["current_node_index"] = 0
        updates["node_results"] = []
        updates["nodes"] = []
        updates["edges"] = []
        updates["refined_nodes"] = []
        updates["plan"] = {}
        updates["intent"] = {}
        updates["task_id"] = ""
        # 清空所有视图残留
        updates["steps"] = [f"需求已修改，重新规划: {cleaned[:50]}"]
        updates["direct_response"] = ""
        updates["error"] = ""
        updates["status_text"] = "🔄 重新规划中"
        updates["cancel_requested"] = False
        updates["user_action"] = None
        updates["svg"] = ""
        updates["flowchart_html"] = ""

    return updates


async def direct_answer_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """直接回答（异步）"""
    rid = set_req_id()
    logger.info("[Graph] direct_answer_node (req=%s)", rid)

    cancel_event = _get_cancel_event(config)
    answer = await direct_chat(state["user_input"], rid, cancel_event=cancel_event)

    try:
        await create_direct_answer_task(
            raw_query=state["user_input"],
            intent_info=state.get("intent", {}),
            response=answer,
            req_id=rid,
        )
    except Exception as e:
        logger.warning("[Graph] 直接回答入库失败(不影响返回): %s", e)

    return {
        "direct_response": answer,
        "messages": [
            {"role": "user", "content": state["user_input"]},
            {"role": "assistant", "content": answer},
        ],
        "steps": state.get("steps", []) + ["直接回答完成"],
    }


async def cancel_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """取消节点（异步）"""
    logger.info("[Graph] cancel_node")
    if state.get("task_id"):
        try:
            await mark_task_failed(state["task_id"], error="用户取消")
        except (RuntimeError, TimeoutError) as e:
            logger.warning("[Graph] cancel_node: mark_task_failed failed: %s", e)

    return {
        "cancel_requested": True,
        "status_text": "⏹️ 已取消",
        "steps": state.get("steps", []) + ["任务已取消"],
    }


# ══════════════════════════════════════════════════
#  路由函数（同步，纯计算）
# ══════════════════════════════════════════════════

def route_after_intent(state: TaskState) -> str:
    if state.get("cancel_requested"):
        return "cancel"
    if not state.get("needs_planning", True):
        return "direct_answer"
    return "plan"


def route_after_execute(state: TaskState) -> str:
    action = state.get("user_action")
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


def route_after_render(state: TaskState) -> str:
    return "execute"


# ══════════════════════════════════════════════════
#  辅助异步函数
# ══════════════════════════════════════════════════

async def _execute_single_node(
    node: dict[str, Any],
    user_input: str,
    intent: dict[str, Any],
    cancel_event: Optional[asyncio.Event] = None,
    req_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    执行单个节点：构建 Prompt + 委托 LLM 调用 + 异常分级响应

    Returns:
        {
            "status": "success" | "failed" | "timeout" | "cancelled",
            "detail": str,
            "retryable": bool,
            "node_id": int | str,
            "node_name": str,
        }
    """
    rid = req_id or "unknown"
    nid = node.get("id", "?")
    name = str(node.get("name", f"步骤{nid}"))
    meta = dict(node.get("meta", {}) or {})

    # ── 1. Prompt 构建 ───────────────────────────────────────────
    prompt = _render_template(
        EXECUTE_NODE_PROMPT, rid,
        user_input=user_input,
        category=str(intent.get("category", "other")),
        name=name,
        detail=str(node.get("details", node.get("detail", ""))),
        preconditions=json.dumps(meta.get("preconditions", []), ensure_ascii=False),
        postconditions=json.dumps(meta.get("postconditions", []), ensure_ascii=False),
    )

    logger.info("[Node] ▶️ 开始执行节点%s: %s (req=%s)", nid, name, rid)

    # ── 2. 委托 LLM 调用 + 异常分级响应 ─────────────────────────
    try:
        success, detail = await execute_node_llm(
            prompt=prompt,
            req_id=rid,
            cancel_event=cancel_event,
            timeout=LLM_NODE_TIMEOUT,
        )

        status = "success" if success else "failed"
        logger.info(
            "[Node] %s 节点%s [%s] 执行%s (req=%s)",
            "✅" if success else "❌", nid, name, status, rid,
        )

        return {
            "status": status,
            "detail": detail,
            "retryable": not success,
            "node_id": nid,
            "node_name": name,
        }

    except LLMCancelledError:
        # 🛑 用户主动取消 → 立即向上抛出，触发整个图优雅停止
        logger.info("[Node] ⏹️ 节点%s [%s] 被用户取消 (req=%s)", nid, name, rid)
        raise

    except LLMTimeoutError as e:
        # ⏰ 超时 → 标记为 timeout，图级别可选择重试该节点或跳过
        logger.warning("[Node] ⏰ 节点%s [%s] 最终超时: %s (req=%s)", nid, name, e, rid)
        return {
            "status": "timeout",
            "detail": "⏰ 执行超时，请稍后重试",
            "retryable": True,
            "node_id": nid,
            "node_name": name,
        }

    except Exception as e:
        # 🔥 真正的未知异常（非 LLM 相关）
        logger.exception("[Node] 🔥 节点%s [%s] 未知异常 (req=%s)", nid, name, rid)
        return {
            "status": "failed",
            "detail": f"💥 未知异常: {type(e).__name__}: {str(e)[:200]}",
            "retryable": False,
            "node_id": nid,
            "node_name": name,
        }
