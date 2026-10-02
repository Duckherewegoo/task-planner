"""refine.py — 节点细化（异步并发）"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import refine_node
from task_planner.infrastructure.logger_setup import (
    get_logger,
    set_req_id,
)

from .sanitize import get_cancel_event

logger = get_logger(__name__)


async def refine_node_fn(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """节点细化（异步并发）"""
    rid = set_req_id()
    logger.info("[Graph] refine_node (req=%s)", rid)

    nodes_raw = state["nodes"]
    intent = state["intent"]
    cancel_event = get_cancel_event(config)

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
