"""plan.py — DAG 规划节点"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import generate_plan
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.utils.context import get_cancel_event

from ._helpers import validate_edge_elements, validate_node_elements

logger = get_logger(__name__)


async def plan_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """规划节点（异步）"""
    rid = set_req_id()
    logger.info("[Graph] plan_node (req=%s)", rid)

    cancel_event = get_cancel_event()
    plan = await generate_plan(
        state["user_input"], state["intent"],
        req_id=rid, cancel_event=cancel_event,
    )

    # Patch A-1：防御 generate_plan 返回非 dict（None / 字符串等）
    # 若不防御，`plan.get(...)` 会以"NoneType has no attribute 'get'"
    # 这种零信息量的错误炸掉，无法定位是 planner prompt 还是模型问题。
    if not isinstance(plan, dict):
        logger.error(
            "[Graph] plan_node 返回非 dict! type=%s raw=%s (req=%s)",
            type(plan).__name__, str(plan)[:500], rid,
        )
        raise ValueError(
            f"规划失败：generate_plan 返回 {type(plan).__name__}，期望 dict (req={rid})"
        )

    nodes_raw = plan.get("nodes", [])
    edges_raw = plan.get("edges", [])

    # Patch A-2：防御非 list——`list("字符串")` 会静默拆成单字符列表，
    # 形成"非空垃圾"，后续节点名全是单字符，下游才会以莫名其妙的方式炸。
    # 这里先验证类型，再复制。
    if not isinstance(nodes_raw, list):
        logger.error(
            "[Graph] plan_node: plan['nodes'] 非 list! type=%s raw=%s (req=%s)",
            type(nodes_raw).__name__, str(nodes_raw)[:500], rid,
        )
        raise ValueError(
            f"规划失败：plan['nodes'] 应为 list，实际为 {type(nodes_raw).__name__} (req={rid})"
        )
    if not isinstance(edges_raw, list):
        logger.error(
            "[Graph] plan_node: plan['edges'] 非 list! type=%s raw=%s (req=%s)",
            type(edges_raw).__name__, str(edges_raw)[:500], rid,
        )
        raise ValueError(
            f"规划失败：plan['edges'] 应为 list，实际为 {type(edges_raw).__name__} (req={rid})"
        )

    # 元素级校验：list[dict] 只保外层——元素可能是 str / None。
    #    refine_node 里 n.get("id") 会在非 dict 元素上崩。
    validate_node_elements(nodes_raw, rid)
    validate_edge_elements(edges_raw, rid)

    # Patch A-3：类型确认后，再做防御式复制——
    # 避免下游节点直接修改 plan 内部的 list 引用。
    nodes_raw = list(nodes_raw)
    edges_raw = list(edges_raw)

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
