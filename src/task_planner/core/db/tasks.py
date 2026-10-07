"""tasks.py — Task CRUD + 状态更新"""
from __future__ import annotations

import time
import uuid
import uuid
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from pymongo.errors import DuplicateKeyError

from task_planner.infrastructure.constants import TASK_STATUS
from task_planner.infrastructure.logger_setup import (
    get_logger,
    get_req_id,
)

from .client import get_db
from .plans import (
    create_plan,
    delete_plan,
    delete_plans,
    get_plan,
)
from .schema import (
    TERMINAL_STATUSES,
    status_name,
    validate_plan,
)

logger = get_logger("task_planner.core.db.tasks")


# ══════════════════════════════════════════════════
#  创建
# ══════════════════════════════════════════════════
async def create_task_with_plan(
    raw_query: str,
    intent_info: dict[str, Any],
    plan_data: dict[str, Any],
    req_id: str = "",
    valid: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    创建任务和关联的计划，返回 (task_doc, plan_doc)。
    失败时尽力回滚已创建的 plan。
    """
    db = await get_db()
    rid = req_id or get_req_id() or ""
    task_id = f"task_{uuid.uuid7().hex}"
    plan_id = f"plan_{task_id}"

    plan_data = validate_plan(plan_data)
    now = datetime.now(UTC)

    task_doc = {
        "task_id": task_id,
        "raw_query": raw_query,
        "intent_info": intent_info,
        "plan_id": plan_id,
        "direct_response": "",
        "status": TASK_STATUS["PENDING"] if valid else TASK_STATUS["FAILED"],
        "valid": valid,
        "retry_count": 0,
        "max_retries": 3,
        "error_msg": "",
        "created_at": now,
        "updated_at": now,
    }

    try:
        plan_doc = await create_plan(plan_id, plan_data, valid, now)
        await db["tasks"].insert_one(task_doc)
        logger.info(
            "[DB] ✅ 创建 Task=%s / Plan=%s (nodes=%d, edges=%d, req=%s)",
            task_id, plan_id, len(plan_doc["nodes"]), len(plan_doc["edges"]), rid,
        )
        return task_doc, plan_doc
    except DuplicateKeyError as e:
        logger.error("[DB] 创建冲突（task_id 或 plan_id 已存在）: %s", e)
        await delete_plan(plan_id)
        raise
    except Exception as e:
        logger.error("[DB] Task 创建失败，回滚 Plan=%s: %s", plan_id, e)
        await delete_plan(plan_id)
        raise


async def create_direct_answer_task(
    raw_query: str,
    intent_info: dict[str, Any],
    response: str,
    req_id: str = "",
) -> dict[str, Any]:
    """为直接回答创建 Task 记录（无 Plan）"""
    db = await get_db()
    rid = req_id or get_req_id() or ""
    task_id = f"task_{uuid.uuid7().hex}"
    now = datetime.now(UTC)

    task_doc = {
        "task_id": task_id,
        "raw_query": raw_query,
        "intent_info": intent_info,
        "plan_id": "",
        "direct_response": response,
        "status": TASK_STATUS["SUCCESS"],
        "valid": True,
        "retry_count": 0,
        "max_retries": 3,
        "error_msg": "",
        "created_at": now,
        "updated_at": now,
    }
    await db["tasks"].insert_one(task_doc)
    logger.info("[DB] ✅ 创建直接回答 Task=%s (req=%s)", task_id, rid)
    return task_doc


# ══════════════════════════════════════════════════
#  查询
# ══════════════════════════════════════════════════
async def get_task(task_id: str) -> dict[str, Any] | None:
    """返回 task 字段字典（补全 schema 默认值，防下游 KeyError）"""
    db = await get_db()
    doc = await db["tasks"].find_one({"task_id": task_id})
    if not doc:
        return None
    doc.pop("_id", None)

    doc.setdefault("task_id", task_id)
    doc.setdefault("raw_query", "")
    doc.setdefault("intent_info", {})
    doc.setdefault("plan_id", "")
    doc.setdefault("direct_response", "")
    doc.setdefault("status", TASK_STATUS["PENDING"])
    doc.setdefault("valid", True)
    doc.setdefault("retry_count", 0)
    doc.setdefault("max_retries", 3)
    doc.setdefault("error_msg", "")
    return doc


async def load_task_with_plan(task_id: str) -> dict[str, Any] | None:
    """
    Task + Plan 一起拿。返回语义：
      - task 不存在 → None
      - task 存在无 plan → {"task": task, "plan": None}
      - task 有 plan_id 但 plan 丢失 → {"task": task, "plan": None, "warning": "plan_lost"}
      - 都有 → {"task": task, "plan": plan}
    """
    task = await get_task(task_id)
    if not task:
        logger.warning("[DB] 任务不存在: %s", task_id)
        return None

    if not task.get("plan_id"):
        return {"task": task, "plan": None}

    plan = await get_plan(task["plan_id"])
    if not plan:
        logger.error("[DB] Plan 丢失: %s", task["plan_id"])
        return {"task": task, "plan": None, "warning": "plan_lost"}

    return {"task": task, "plan": plan}


async def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    """返回最近任务列表（含直接回答）"""
    db = await get_db()
    cursor = db["tasks"].find({}, sort=[("created_at", -1)]).limit(limit)
    results = []
    async for t in cursor:
        t.pop("_id", None)
        intent_info = t.get("intent_info", {}) or {}
        summary = intent_info.get("summary", "")
        title = summary or (t.get("raw_query", "") or "")[:30]
        has_plan = bool(t.get("plan_id"))
        prefix = "📋" if has_plan else "💬"

        created_at = t.get("created_at")
        if isinstance(created_at, datetime):
            created_str = created_at.strftime("%Y-%m-%d %H:%M")
        elif isinstance(created_at, str):
            created_str = created_at[:16]
        else:
            created_str = ""

        results.append({
            "task_id": t["task_id"],
            "title": f"{prefix} {title}",
            "status": t.get("status", 0),
            "valid": t.get("valid", True),
            "retry": f"{t.get('retry_count', 0)}/{t.get('max_retries', 3)}",
            "has_plan": has_plan,
            "created": created_str,
        })
    return results


async def get_recent_tasks(limit: int = 50, rid_tag: str = "query") -> list[str]:
    """返回最近任务摘要字符串列表"""
    db = await get_db()
    cursor = db["tasks"].find({}, sort=[("created_at", -1)]).limit(limit)
    results = []
    async for t in cursor:
        intent_info = t.get("intent_info", {}) or {}
        summary = intent_info.get("summary", "")
        title = summary or (t.get("raw_query", "") or "")[:30]
        status_str = status_name(t.get("status", 0))
        has_plan = bool(t.get("plan_id"))
        prefix = "📋" if has_plan else "💬"
        results.append(f"[{status_str}] {prefix} {title} | {t['task_id']}")
    logger.debug("[DB] 查询最近任务 %d 条 (tag=%s)", len(results), rid_tag)
    return results


# ══════════════════════════════════════════════════
#  状态更新
# ══════════════════════════════════════════════════
async def mark_task_success(task_id: str) -> None:
    """终态判断补全 TIMEOUT / SKIPPED"""
    db = await get_db()
    result = await db["tasks"].update_one(
        {"task_id": task_id, "status": {"$nin": list(TERMINAL_STATUSES)}},
        {"$set": {
            "status": TASK_STATUS["SUCCESS"],
            "updated_at": datetime.now(UTC),
        }},
    )
    if result.modified_count == 0:
        logger.warning("[DB] mark_task_success: 任务 %s 已是终态或不存在", task_id)


async def mark_task_failed(task_id: str, error: str = "") -> None:
    db = await get_db()
    await db["tasks"].update_one(
        {"task_id": task_id},
        {"$set": {
            "status": TASK_STATUS["FAILED"],
            "error_msg": error,
            "updated_at": datetime.now(UTC),
        }},
    )


async def mark_task_timeout(task_id: str) -> None:
    db = await get_db()
    await db["tasks"].update_one(
        {"task_id": task_id},
        {"$set": {
            "status": TASK_STATUS["TIMEOUT"],
            "error_msg": "超过总时限",
            "updated_at": datetime.now(UTC),
        }},
    )


async def mark_task_running(task_id: str) -> None:
    db = await get_db()
    await db["tasks"].update_one(
        {"task_id": task_id},
        {"$set": {
            "status": TASK_STATUS["RUNNING"],
            "updated_at": datetime.now(UTC),
        }},
    )


# ══════════════════════════════════════════════════
#  删除
# ══════════════════════════════════════════════════
async def delete_task(task_id: str) -> bool:
    """删除任务并级联删除 Plan"""
    db = await get_db()
    task = await db["tasks"].find_one({"task_id": task_id})
    if not task:
        logger.warning("[DB] 删除失败，任务不存在: %s", task_id)
        return False
    if task.get("plan_id"):
        await delete_plan(task["plan_id"])
        logger.info("[DB] 级联删除 Plan=%s", task["plan_id"])
    await db["tasks"].delete_one({"task_id": task_id})
    logger.info("[DB] 删除任务 %s", task_id)
    return True


async def batch_delete_tasks(task_ids: list[str]) -> int:
    """批量删除任务，返回删除数量"""
    if not task_ids:
        return 0
    db = await get_db()
    cursor = db["tasks"].find(
        {"task_id": {"$in": task_ids}}, {"plan_id": 1}
    )
    plan_ids = []
    async for t in cursor:
        if t.get("plan_id"):
            plan_ids.append(t["plan_id"])
    if plan_ids:
        deleted = await delete_plans(plan_ids)
        logger.info("[DB] 级联删除 %d 个 Plan", deleted)
    result = await db["tasks"].delete_many({"task_id": {"$in": task_ids}})
    logger.info("[DB] 批量删除 %d 个任务", result.deleted_count)
    return result.deleted_count


__all__ = [
    "create_task_with_plan",
    "create_direct_answer_task",
    "get_task",
    "load_task_with_plan",
    "list_tasks",
    "get_recent_tasks",
    "mark_task_success",
    "mark_task_failed",
    "mark_task_timeout",
    "mark_task_running",
    "delete_task",
    "batch_delete_tasks",
]
