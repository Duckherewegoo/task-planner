"""plans.py — Plan 独立 CRUD"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from task_planner.infrastructure.logger_setup import get_logger

from .client import get_db

logger = get_logger("task_planner.core.db.plans")


async def create_plan(
    plan_id: str,
    plan_data: dict[str, Any],
    valid: bool,
    now: datetime,
) -> dict[str, Any]:
    """插入 Plan 文档，返回被插入的 dict"""
    db = await get_db()
    plan_doc = {
        "plan_id": plan_id,
        "graph_title": plan_data.get("task_name", "未命名计划"),
        "description": plan_data.get("description", ""),
        "nodes": plan_data.get("nodes", []),
        "edges": plan_data.get("edges", []),
        "valid": valid,
        "modified_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db["plans"].insert_one(plan_doc)
    return plan_doc


async def get_plan(plan_id: str) -> dict[str, Any] | None:
    """返回 plan 字段字典（去除 _id）"""
    db = await get_db()
    doc = await db["plans"].find_one({"plan_id": plan_id})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc


async def delete_plan(plan_id: str) -> bool:
    """删除单个 Plan"""
    db = await get_db()
    result = await db["plans"].delete_one({"plan_id": plan_id})
    return result.deleted_count > 0


async def delete_plans(plan_ids: list[str]) -> int:
    """批量删除 Plan，返回删除数量"""
    if not plan_ids:
        return 0
    db = await get_db()
    result = await db["plans"].delete_many({"plan_id": {"$in": plan_ids}})
    return result.deleted_count


__all__ = ["create_plan", "get_plan", "delete_plan", "delete_plans"]
