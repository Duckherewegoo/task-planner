"""nodes.py — Plan 内 nodes 数组的状态操作"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from task_planner.infrastructure.constants import TASK_STATUS
from task_planner.infrastructure.logger_setup import get_logger

from .client import get_db
from .schema import status_name

logger = get_logger("task_planner.db.nodes")


async def update_node_status(
    task_id: str,
    node_id: int,
    new_status: int,
    details: str = "",
) -> bool:
    """更新指定节点的状态和 details（数组过滤器，原子操作）"""
    db = await get_db()
    task = await db["tasks"].find_one({"task_id": task_id})
    if not task:
        logger.warning("[DB] update_node_status: 任务不存在 %s", task_id)
        return False
    plan_id = task.get("plan_id")
    if not plan_id:
        logger.error("[DB] update_node_status: 任务无 Plan %s", task_id)
        return False

    now = datetime.now(timezone.utc)
    result = await db["plans"].update_one(
        {"plan_id": plan_id, "nodes.id": node_id},
        {
            "$set": {
                "nodes.$.status": new_status,
                "nodes.$.details": details,
                "nodes.$.updated_at": now,
                "updated_at": now,
            },
            "$inc": {"modified_count": 1},
        },
    )
    if result.modified_count == 0:
        logger.warning("[DB] update_node_status: 节点 #%d 未找到", node_id)
        return False
    logger.info(
        "[DB] 节点状态更新 %s#%d → %s",
        task_id, node_id, status_name(new_status),
    )
    return True


async def reset_node_status(task_id: str, node_id: int) -> bool:
    """将节点状态重置为 PENDING(0)"""
    db = await get_db()
    task = await db["tasks"].find_one({"task_id": task_id})
    if not task:
        logger.warning("[DB] reset_node_status: 任务不存在 %s", task_id)
        return False
    plan_id = task.get("plan_id")
    if not plan_id:
        logger.error("[DB] reset_node_status: 任务无 Plan %s", task_id)
        return False

    now = datetime.now(timezone.utc)
    result = await db["plans"].update_one(
        {"plan_id": plan_id, "nodes.id": node_id},
        {
            "$set": {
                "nodes.$.status": TASK_STATUS["PENDING"],
                "nodes.$.error_msg": "",
                "nodes.$.updated_at": now,
                "updated_at": now,
            },
            "$inc": {"modified_count": 1},
        },
    )
    if result.modified_count == 0:
        logger.warning("[DB] reset_node_status: 节点 #%d 未找到", node_id)
        return False
    logger.info("[DB] 节点重置 %s#%d → PENDING", task_id, node_id)
    return True


async def get_node_status(task_id: str, node_id: int) -> Optional[int]:
    """查询节点状态"""
    db = await get_db()
    task = await db["tasks"].find_one({"task_id": task_id})
    if not task:
        return None
    plan_id = task.get("plan_id")
    if not plan_id:
        return None

    plan = await db["plans"].find_one(
        {"plan_id": plan_id, "nodes.id": node_id},
        {"nodes.$": 1},
    )
    if not plan:
        return None
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or len(nodes) == 0:
        return None
    return nodes[0].get("status", 0)


__all__ = ["update_node_status", "reset_node_status", "get_node_status"]
