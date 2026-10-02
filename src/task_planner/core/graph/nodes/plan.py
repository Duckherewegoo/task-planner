"""plan.py — DAG 规划节点"""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import generate_plan
from task_planner.infrastructure.logger_setup import (
    get_logger,
    set_req_id,
)

from .sanitize import get_cancel_event

logger = get_logger(__name__)


async def plan_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """规划节点（异步）"""
    rid = set_req_id()
    logger.info("[Graph] plan_node (req=%s)", rid)

    cancel_event = get_cancel_event(config)
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
        "steps": state.get("steps", []) + [
            f"规划完成: {len(nodes_raw)}个节点, {len(edges_raw)}条边"
        ],
    }
