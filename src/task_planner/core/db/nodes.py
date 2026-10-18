"""nodes.py — Plan 内 nodes 数组的状态操作"""

from datetime import UTC, datetime

from task_planner.infrastructure.constants import TASK_STATUS
from task_planner.infrastructure.logger_setup import get_logger

from ._helpers import resolve_plan_id
from .client import get_db
from .schema import status_name

logger = get_logger("task_planner.core.db.nodes")


# ═══════════════════════════════════════════════════════════════════
#  写操作
# ═══════════════════════════════════════════════════════════════════
async def update_node_status(
    task_id: str,
    node_id: int,
    new_status: int,
    details: str = "",
) -> bool:
    """
    更新指定节点的状态和 details（数组过滤器，原子操作）。

    返回 True 表示节点存在且已更新；False 表示 task/plan/node 不存在。
    """
    plan_id = await resolve_plan_id(task_id, op="update_node_status")
    if not plan_id:
        return False

    db = get_db()
    now = datetime.now(UTC)
    result = db["plans"].update_one(
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

    # 用 matched_count 而非 modified_count：
    #    语义是"找到了节点吗"，而不是"值变了吗"
    if result.matched_count == 0:
        logger.warning("[DB] update_node_status: 节点 #%d 未找到", node_id)
        return False

    logger.info(
        "[DB] 节点状态更新 %s#%d → %s",
        task_id, node_id, status_name(new_status),
    )
    return True


async def reset_node_status(task_id: str, node_id: int) -> bool:
    """
    将节点状态重置为 PENDING(0)，并清空 error_msg。

    返回 True 表示节点存在且已重置；False 表示 task/plan/node 不存在。
    """
    plan_id = await resolve_plan_id(task_id, op="reset_node_status")
    if not plan_id:
        return False

    db = get_db()
    now = datetime.now(UTC)
    result = db["plans"].update_one(
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

    if result.matched_count == 0:
        logger.warning("[DB] reset_node_status: 节点 #%d 未找到", node_id)
        return False

    logger.info("[DB] 节点重置 %s#%d → PENDING", task_id, node_id)
    return True


# ═══════════════════════════════════════════════════════════════════
#  读操作
# ═══════════════════════════════════════════════════════════════════
async def get_node_status(task_id: str, node_id: int) -> int | None:
    """
    查询节点状态。

    返回 int（状态码）；task/plan/node 任一层缺失则返回 None。
    只读操作，不打 warning（quiet=True）。
    """
    plan_id = await resolve_plan_id(
        task_id, op="get_node_status", quiet=True,
    )
    if not plan_id:
        return None

    db = get_db()
    # 用 $ 投影，只返回匹配的那个节点（大 DAG 省 99% 带宽）
    plan = db["plans"].find_one(
        {"plan_id": plan_id, "nodes.id": node_id},
        {"nodes.$": 1},
    )
    if not plan:
        return None

    nodes = plan.get("nodes")
    # 三重防御：非空 + 是 list + 至少一个元素
    if not isinstance(nodes, list) or len(nodes) == 0:
        return None

    return nodes[0].get("status", 0)


__all__ = ["update_node_status", "reset_node_status", "get_node_status"]
