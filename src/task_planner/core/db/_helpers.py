"""
_helpers.py — db 包内部共享工具（私有模块）
═══════════════════════════════════════════════════════════════════════
本模块的函数仅供 core/db 包内其他模块使用，不对外导出。

设计动机：
    update_node_status / reset_node_status / get_node_status
    三个函数都需要"从 task_id 解析出 plan_id"这同一段逻辑，
    抽到此处避免重复。
"""

from task_planner.infrastructure.logger_setup import get_logger

from .client import get_db

logger = get_logger("task_planner.core.db._helpers")


async def resolve_plan_id(
    task_id: str,
    op: str = "操作",
    *,
    quiet: bool = False,
) -> str | None:
    """
    从 task_id 解析出 plan_id。

    Args:
        task_id: 任务 ID
        op:      调用来源标识（用于日志区分，如 "update_node_status"）
        quiet:   True 时不打日志——供只读函数使用，避免查询不存在的
                 task 时刷 warning

    Returns:
        plan_id 字符串；task 不存在 或 task 无 plan_id 时返回 None
    """
    db = get_db()
    task = db["tasks"].find_one({"task_id": task_id})

    if not task:
        if not quiet:
            logger.warning("[DB] %s: 任务不存在 %s", op, task_id)
        return None

    plan_id = task.get("plan_id")
    if not plan_id:
        if not quiet:
            logger.error("[DB] %s: 任务无 Plan %s", op, task_id)
        return None

    return plan_id


__all__ = ["resolve_plan_id"]
