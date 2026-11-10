"""plan.py — DAG 规划节点"""

import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import generate_plan
from task_planner.infrastructure.logger_setup import get_logger, set_req_id
from task_planner.utils.context import get_cancel_event

from ._helpers import validate_edge_elements, validate_node_elements

logger = get_logger(__name__)


def _format_few_shot(examples: list[dict]) -> str:
    """把 retrieved_examples 拼成 few-shot 文本。

    空列表返回空字符串——调用方据此判断"无 few-shot 上下文"。
    任何字段缺失都降级为空字符串，不抛异常——few-shot 是增强，不是硬依赖。
    """
    if not examples:
        return ""

    blocks: list[str] = []
    for i, ex in enumerate(examples, 1):
        if not isinstance(ex, dict):
            continue
        user_input = str(ex.get("user_input", "")).strip()
        plan = ex.get("expected_plan")
        if not isinstance(plan, dict):
            plan = {}
        nodes = plan.get("nodes") or []
        edges = plan.get("edges") or []
        blocks.append(
            f"### 示例 {i}\n"
            f"用户需求：{user_input}\n"
            f"参考计划：\n"
            f"- 任务名：{plan.get('task_name', '')}\n"
            f"- 描述：{plan.get('description', '')}\n"
            f"- 节点：{json.dumps(nodes, ensure_ascii=False)}\n"
            f"- 边：{json.dumps(edges, ensure_ascii=False)}"
        )

    return "\n\n".join(blocks)


async def plan_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """规划节点（异步）"""
    rid = set_req_id()
    logger.info("[Graph] plan_node (req=%s)", rid)

    cancel_event = get_cancel_event()

    # ── 消费 retrieve 节点的产物（若存在）──
    # state.get(...) 而非 state[...]：retrieved_examples 是 NotRequired，
    # retrieve 未挂载时字段可能不存在。
    examples = state.get("retrieved_examples") or []
    few_shot_text = _format_few_shot(examples)
    if few_shot_text:
        logger.info(
            "[Graph] plan_node 注入 few-shot: %d 条案例 (req=%s)",
            len(examples),
            rid,
        )
    else:
        logger.debug("[Graph] plan_node 无 few-shot 上下文 (req=%s)", rid)

    plan = await generate_plan(
        state["user_input"],
        state["intent"],
        req_id=rid,
        cancel_event=cancel_event,
        few_shot_text=few_shot_text,
    )

    # Patch A-1：防御 generate_plan 返回非 dict
    if not isinstance(plan, dict):
        logger.error(
            "[Graph] plan_node 返回非 dict! type=%s raw=%s (req=%s)",
            type(plan).__name__,
            str(plan)[:500],
            rid,
        )
        raise ValueError(
            f"规划失败：generate_plan 返回 {type(plan).__name__}，期望 dict (req={rid})"
        )

    nodes_raw = plan.get("nodes", [])
    edges_raw = plan.get("edges", [])

    # Patch A-2：防御非 list
    if not isinstance(nodes_raw, list):
        logger.error(
            "[Graph] plan_node: plan['nodes'] 非 list! type=%s raw=%s (req=%s)",
            type(nodes_raw).__name__,
            str(nodes_raw)[:500],
            rid,
        )
        raise ValueError(
            f"规划失败：plan['nodes'] 应为 list，实际为 {type(nodes_raw).__name__} (req={rid})"
        )
    if not isinstance(edges_raw, list):
        logger.error(
            "[Graph] plan_node: plan['edges'] 非 list! type=%s raw=%s (req=%s)",
            type(edges_raw).__name__,
            str(edges_raw)[:500],
            rid,
        )
        raise ValueError(
            f"规划失败：plan['edges'] 应为 list，实际为 {type(edges_raw).__name__} (req={rid})"
        )

    # 元素级校验
    validate_node_elements(nodes_raw, rid)
    validate_edge_elements(edges_raw, rid)

    # Patch A-3：防御式复制
    nodes_raw = list(nodes_raw)
    edges_raw = list(edges_raw)

    if not nodes_raw:
        logger.error(
            "[Graph] plan_node 产出空计划! raw_plan=%s (req=%s)",
            str(plan)[:500],
            rid,
        )
        raise ValueError(
            f"规划失败：LLM 未返回有效节点。请检查 planner prompt 或模型输出格式。(req={rid})"
        )

    logger.info(
        "[Graph] plan_node: %d nodes, %d edges (req=%s)",
        len(nodes_raw),
        len(edges_raw),
        rid,
    )

    return {
        "plan": plan,
        "nodes": nodes_raw,
        "edges": edges_raw,
        "steps": state.get("steps", [])
        + [f"规划完成: {len(nodes_raw)}个节点, {len(edges_raw)}条边"],
    }
