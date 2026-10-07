"""
_executor.py — 单节点执行器（nodes 包内部使用）。
对外只暴露 execute_single_node()。
"""
from __future__ import annotations

import asyncio
import json
from typing import (
    Any,
)

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.llm import (
    LLMCancelledError,
    LLMTimeoutError,
    execute_node_llm,
)
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.prompts.loader import (
    EXECUTE_NODE_PROMPT,
    render_template,
)

logger = get_logger(__name__)


async def execute_single_node(
    node: dict[str, Any],
    user_input: str,
    intent: dict[str, Any],
    cancel_event: asyncio.Event | None = None,
    req_id: str | None = None,
) -> dict[str, Any]:
    """
    执行单个节点：构建 Prompt + 委托 LLM + 异常分级响应

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

    prompt = render_template(
        EXECUTE_NODE_PROMPT, rid,
        user_input=user_input,
        category=str(intent.get("category", "other")),
        name=name,
        detail=str(node.get("details", node.get("detail", ""))),
        preconditions=json.dumps(meta.get("preconditions", []), ensure_ascii=False),
        postconditions=json.dumps(meta.get("postconditions", []), ensure_ascii=False),
    )

    logger.info("[Node] ▶️ 开始执行节点%s: %s (req=%s)", nid, name, rid)

    try:
        success, detail = await execute_node_llm(
            prompt=prompt,
            req_id=rid,
            cancel_event=cancel_event,
            timeout=_hub.dev.LLM_NODE_TIMEOUT,
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
        # 用户取消 → 向上抛出，触发图优雅停止
        logger.info("[Node] ⏹️ 节点%s [%s] 被用户取消 (req=%s)", nid, name, rid)
        raise

    except LLMTimeoutError as e:
        logger.warning("[Node] ⏰ 节点%s [%s] 最终超时: %s (req=%s)", nid, name, e, rid)
        return {
            "status": "timeout",
            "detail": "⏰ 执行超时，请稍后重试",
            "retryable": True,
            "node_id": nid,
            "node_name": name,
        }

    except Exception as e:
        logger.exception("[Node] 🔥 节点%s [%s] 未知异常 (req=%s)", nid, name, rid)
        return {
            "status": "failed",
            "detail": f"💥 未知异常: {type(e).__name__}: {str(e)[:200]}",
            "retryable": False,
            "node_id": nid,
            "node_name": name,
        }
