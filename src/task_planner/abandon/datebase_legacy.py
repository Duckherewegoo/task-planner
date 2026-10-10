"""
database.py — MongoDB 异步数据层（Motor 驱动版）
====================================================
所有函数均为 async def，使用 AsyncIOMotorClient。
连接延迟初始化，线程安全（协程安全）。

Changelog:
  ✅ P0-1：mark_task_success 终态判断补全（TIMEOUT/SKIPPED 不再被覆盖）
  ✅ P0-2：init_db 失败时清理半成品 client，避免连接池泄漏
  ✅ P0-3：get_node_status 边界检查更严格
  ✅ P0-4：nodes[].updated_at 统一用 datetime（与顶层一致）
  ✅ P1-1：_validate_plan 返回新 dict，不再原地修改入参
  ✅ P1-2：get_task 补全 schema 默认值
  ✅ P1-3：load_task_with_plan 区分"未找到"与"数据损坏"
  ✅ P1-5：logger 改用 get_logger（绑定 QueueListener）
  ✅ P1-6：_validate_plan 丢弃的边记录原因
  ✅ P2-1：删除死导入
  ✅ P2-2：_STATUS_NAMES 改为模块级常量
  ✅ P2-3：init_db 加 serverSelectionTimeoutMS
  ✅ P2-4：新增 close_db()
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import DuplicateKeyError

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import TASK_STATUS

MONGO_DB = _hub.dev.MONGO_DB
MONGO_HOST = _hub.dev.MONGO_HOST
MONGO_PORT = _hub.dev.MONGO_PORT
from task_planner.infrastructure.logger_setup import get_logger, get_req_id

logger = get_logger("task_planner.db")


# ✅ P2-2 修复：模块级常量，不再懒加载
_STATUS_NAMES: dict[int, str] = {v: k for k, v in TASK_STATUS.items()}

# ✅ P0-1 修复：明确的终态集合
_TERMINAL_STATUSES = frozenset({
    TASK_STATUS["SUCCESS"],   # 2
    TASK_STATUS["FAILED"],    # 3
    TASK_STATUS["TIMEOUT"],   # 4
    TASK_STATUS["SKIPPED"],   # 5
})


def _status_name(code: int) -> str:
    return _STATUS_NAMES.get(code, f"?({code})")


# ── 异步客户端单例 ──────────────────────────────
_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None
_db_lock = asyncio.Lock()
_initialized = False


# ══════════════════════════════════════════════════
#  连接生命周期
# ══════════════════════════════════════════════════

async def init_db() -> None:
    """异步初始化 MongoDB 连接，创建索引"""
    global _client, _db, _initialized
    if _initialized:
        return
    async with _db_lock:
        if _initialized:
            return

        # ✅ P0-2 修复：先构造本地变量，全部成功后再赋给全局
        new_client = None
        try:
            new_client = AsyncIOMotorClient(
                host=MONGO_HOST,
                port=MONGO_PORT,
                maxPoolSize=100,
                minPoolSize=10,
                # ✅ P2-3 修复：显式超时，避免默认 30s hang
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
            )
            new_db = new_client[MONGO_DB]
            await new_db.command("ping")
            await _create_indexes_on(new_db)

            _client = new_client
            _db = new_db
            _initialized = True
            logger.info(
                "[DB] ✅ MongoDB 异步连接成功 (%s:%d/%s)",
                MONGO_HOST, MONGO_PORT, MONGO_DB,
            )
        except Exception as e:
            # ✅ P0-2 修复：失败时清理半成品
            if new_client is not None:
                try:
                    new_client.close()
                except Exception:
                    pass
            logger.error("[DB] ❌ MongoDB 连接失败: %s", e)
            raise


async def _create_indexes_on(db: AsyncIOMotorDatabase) -> None:
    """在指定 db 上创建索引"""
    tasks = db["tasks"]
    await tasks.create_indexes([
        IndexModel([("task_id", ASCENDING)], unique=True),
        IndexModel([("created_at", DESCENDING)]),
        IndexModel([("status", ASCENDING)]),
    ])
    plans = db["plans"]
    await plans.create_indexes([
        IndexModel([("plan_id", ASCENDING)], unique=True),
        IndexModel([("created_at", DESCENDING)]),
    ])


async def get_db() -> AsyncIOMotorDatabase:
    """获取数据库实例（自动初始化）"""
    if not _initialized:
        await init_db()
    if _db is None:
        raise RuntimeError("[DB] 数据库未初始化")
    return _db


async def close_db() -> None:
    """✅ P2-4 新增：显式关闭连接（用于测试/进程退出）"""
    global _client, _db, _initialized
    async with _db_lock:
        if _client is not None:
            try:
                _client.close()
                logger.info("[DB] MongoDB 连接已关闭")
            except Exception as e:
                logger.warning("[DB] 关闭连接失败: %s", e)
        _client = None
        _db = None
        _initialized = False


# ══════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════

def _safe_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _validate_plan(plan_data: dict[str, Any]) -> dict[str, Any]:
    """
    清洗 nodes/edges，确保 ID 合法，并补全字段。

    ✅ P1-1 修复：返回新 dict，不再原地修改入参。
    ✅ P1-6 修复：丢弃的边记录原因。
    """
    nodes_raw = list(plan_data.get("nodes", []) or [])
    edges_raw = list(plan_data.get("edges", []) or [])

    clean_nodes = []
    for i, n in enumerate(nodes_raw, 1):
        clean_nodes.append({
            "id": _safe_int(n.get("id"), default=i),
            "name": str(n.get("name", f"步骤{i}")),
            "details": str(n.get("details", "") or n.get("detail", "")),
            "status": _safe_int(n.get("status"), default=0),
        })

    id_set = {n["id"] for n in clean_nodes}
    clean_edges = []
    for e in edges_raw:
        f = _safe_int(e.get("from"), default=0)
        t = _safe_int(e.get("to"), default=0)

        if f not in id_set:
            logger.warning("[DB] 丢弃无效边（from 节点不存在）: %s→%s", f, t)
            continue
        if t not in id_set:
            logger.warning("[DB] 丢弃无效边（to 节点不存在）: %s→%s", f, t)
            continue
        if f == t:
            logger.warning("[DB] 丢弃自环边: %s→%s", f, t)
            continue

        clean_edges.append({
            "from": f,
            "to": t,
            "label": str(e.get("label", "")),
        })

    logger.debug(
        "[DB] Plan 清洗完成: nodes=%d(原始%d), edges=%d(原始%d, 丢弃%d)",
        len(clean_nodes), len(nodes_raw),
        len(clean_edges), len(edges_raw),
        len(edges_raw) - len(clean_edges),
    )

    # ✅ P1-1 修复：返回新 dict
    return {
        **plan_data,
        "nodes": clean_nodes,
        "edges": clean_edges,
        "task_name": plan_data.get("task_name", "未命名计划"),
        "description": plan_data.get("description", ""),
    }


# ══════════════════════════════════════════════════
#  公开 API（全部异步）
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
    task_id = f"task_{int(time.time() * 1000)}"
    plan_id = f"plan_{task_id}"

    # ✅ P1-1：_validate_plan 返回新 dict
    plan_data = _validate_plan(plan_data)

    now = datetime.now(timezone.utc)

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
        await db["plans"].insert_one(plan_doc)
        await db["tasks"].insert_one(task_doc)
        logger.info(
            "[DB] ✅ 创建 Task=%s / Plan=%s (nodes=%d, edges=%d, req=%s)",
            task_id, plan_id, len(plan_doc["nodes"]), len(plan_doc["edges"]), rid,
        )
        return task_doc, plan_doc
    except DuplicateKeyError as e:
        logger.error("[DB] 创建冲突（task_id 或 plan_id 已存在）: %s", e)
        await db["plans"].delete_one({"plan_id": plan_id})
        raise
    except Exception as e:
        logger.error("[DB] Task 创建失败，回滚 Plan=%s: %s", plan_id, e)
        await db["plans"].delete_one({"plan_id": plan_id})
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
    task_id = f"task_{int(time.time() * 1000)}"
    now = datetime.now(timezone.utc)

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


async def get_task(task_id: str) -> Optional[dict[str, Any]]:
    """
    返回 task 字段字典。

    ✅ P1-2 修复：补全所有 schema 字段的默认值，避免下游 KeyError。
    """
    db = await get_db()
    doc = await db["tasks"].find_one({"task_id": task_id})
    if not doc:
        return None
    doc.pop("_id", None)

    # 补全 schema 默认值
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


async def get_plan(plan_id: str) -> Optional[dict[str, Any]]:
    """返回 plan 字段字典"""
    db = await get_db()
    doc = await db["plans"].find_one({"plan_id": plan_id})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc


async def load_task_with_plan(task_id: str) -> Optional[dict[str, Any]]:
    """
    Task + Plan 一起拿。

    ✅ P1-3 修复：区分三种返回语义
      - task 不存在       → None
      - task 存在无 plan  → {"task": task, "plan": None}
      - task + plan 存在  → {"task": task, "plan": plan}
      - task 有 plan_id 但 plan 丢失 → {"task": task, "plan": None, "warning": "plan_lost"}
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

        # ✅ P2-5 修复：兼容 created_at 是字符串或缺失的情况
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
        status_name = _status_name(t.get("status", 0))
        has_plan = bool(t.get("plan_id"))
        prefix = "📋" if has_plan else "💬"
        results.append(f"[{status_name}] {prefix} {title} | {t['task_id']}")
    logger.debug("[DB] 查询最近任务 %d 条 (tag=%s)", len(results), rid_tag)
    return results


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
                # ✅ P0-4 修复：统一用 datetime，不用 isoformat
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
        task_id, node_id, _status_name(new_status),
    )
    return True


async def mark_task_success(task_id: str) -> None:
    """✅ P0-1 修复：终态判断补全 TIMEOUT / SKIPPED"""
    db = await get_db()
    result = await db["tasks"].update_one(
        {"task_id": task_id, "status": {"$nin": list(_TERMINAL_STATUSES)}},
        {"$set": {
            "status": TASK_STATUS["SUCCESS"],
            "updated_at": datetime.now(timezone.utc),
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
            "updated_at": datetime.now(timezone.utc),
        }},
    )


async def mark_task_timeout(task_id: str) -> None:
    db = await get_db()
    await db["tasks"].update_one(
        {"task_id": task_id},
        {"$set": {
            "status": TASK_STATUS["TIMEOUT"],
            "error_msg": "超过总时限",
            "updated_at": datetime.now(timezone.utc),
        }},
    )


async def mark_task_running(task_id: str) -> None:
    db = await get_db()
    await db["tasks"].update_one(
        {"task_id": task_id},
        {"$set": {
            "status": TASK_STATUS["RUNNING"],
            "updated_at": datetime.now(timezone.utc),
        }},
    )


async def delete_task(task_id: str) -> bool:
    """删除任务并级联删除 Plan"""
    db = await get_db()
    task = await db["tasks"].find_one({"task_id": task_id})
    if not task:
        logger.warning("[DB] 删除失败，任务不存在: %s", task_id)
        return False
    if task.get("plan_id"):
        await db["plans"].delete_one({"plan_id": task["plan_id"]})
        logger.info("[DB] 级联删除 Plan=%s", task["plan_id"])
    await db["tasks"].delete_one({"task_id": task_id})
    logger.info("[DB] 删除任务 %s", task_id)
    return True


async def batch_delete_tasks(task_ids: list[str]) -> int:
    """批量删除任务，返回删除数量"""
    if not task_ids:
        return 0
    db = await get_db()
    cursor = db["tasks"].find({"task_id": {"$in": task_ids}}, {"plan_id": 1})
    plan_ids = []
    async for t in cursor:
        if t.get("plan_id"):
            plan_ids.append(t["plan_id"])
    if plan_ids:
        await db["plans"].delete_many({"plan_id": {"$in": plan_ids}})
        logger.info("[DB] 级联删除 %d 个 Plan", len(plan_ids))
    result = await db["tasks"].delete_many({"task_id": {"$in": task_ids}})
    deleted_count = result.deleted_count
    logger.info("[DB] 批量删除 %d 个任务", deleted_count)
    return deleted_count


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
                # ✅ P0-4 修复：统一 datetime
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
    # ✅ P0-3 修复：更严格的边界检查
    if not plan:
        return None
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or len(nodes) == 0:
        return None
    return nodes[0].get("status", 0)


# ══════════════════════════════════════════════════
#  兼容 DBManager 类
# ══════════════════════════════════════════════════

class DBManager:
    async def get_recent_tasks(
        self, limit: int = 50, rid_tag: str = "query",
    ) -> list[str]:
        return await get_recent_tasks(limit, rid_tag)

    async def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        return await list_tasks(limit)


db_manager = DBManager()
