"""render.py — 流程图渲染节点（CPU 密集，用 to_thread）"""

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
    try:
        html = await asyncio.to_thread(
            flowchart_pro.render_interactive,
            state["nodes"],
            state["edges"],
            state["task_id"],
            rid,
         )
    except Exception as e:
        # 渲染失败不阻断执行链：留空 HTML，下游 execute 照常跑
        logger.warning(
            "[Graph] render_node 渲染失败(不影响执行): %s (req=%s)", e, rid
        )
        html = ""


    return {
        "flowchart_html": html,
        "steps": state.get("steps", []) + [
            "流程图渲染完成" if html else "流程图渲染失败(已跳过)"
        ],
     }
