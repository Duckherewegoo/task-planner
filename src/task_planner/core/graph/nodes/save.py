"""save.py — 入库节点（DB 不可用时降级为本地 ID）"""
from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.db import create_task_with_plan
from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

logger = get_logger(__name__)


async def save_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """
    入库节点（异步）。

    降级策略：DB 不可用时不再让整个图崩溃，而是：
      - 生成一个本地 task_id（uuid 前缀标记）
      - 记录 warning 日志
      - 继续后续节点（render / execute）

    这样评估、CI、本地 demo 无需 MongoDB 也能跑通。
    """
    rid = set_req_id()
    logger.info("[Graph] save_node (req=%s)", rid)

    try:
        task_doc, _plan_doc = await create_task_with_plan(
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
        fallback_id = f"local-{uuid.uuid4().hex[:12]}"
        logger.warning(
            "[Graph] save_node DB 写入失败，降级为本地 ID: %s | err=%s",
            fallback_id, e,
        )
        return {
            "task_id": fallback_id,
            "steps": state.get("steps", []) + [f"⚠️ 未入库(降级): {fallback_id}"],
            "error": "",  # 不污染 error 字段，图继续执行
        }
