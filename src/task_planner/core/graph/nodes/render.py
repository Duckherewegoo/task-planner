"""render.py — 流程图渲染节点（CPU 密集，用 to_thread）"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

logger = get_logger(__name__)


async def render_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """渲染流程图"""
    rid = set_req_id()
    logger.info("[Graph] render_node (req=%s)", rid)

    # 延迟导入：flowchart_pro 依赖 pyvis/cairosvg，启动时不需要
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
