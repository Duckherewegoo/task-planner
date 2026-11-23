"""client.py — MongoDB 连接生命周期管理（异步，适配 Dash 4.2 + FastAPI backend）

⚠️ 为什么是 Motor 异步：
   本项目已迁移到 Dash 4.2 + FastAPI backend，回调跑在 uvicorn 的持久
   event loop 里，不再是旧 WSGI backend「每回调 asyncio.run 一个新 loop」
   的模式。Motor 的 AsyncIOMotorClient 可以在同一 loop 内跨回调复用，
   连接池不会爆炸。

   ⚠️ 前提：init_db 必须首次在 uvicorn 的 event loop 内调用。Motor 在首次
   await 操作时把 client 绑到当前 loop；绑错 loop 后跨 loop 用会报
   "attached to a different loop"。懒初始化（get_db 首次触发）天然满足。

⚠️ 关于清理：
   FastAPI shutdown 事件 await close_db()；atexit 用同步的 _atexit_close()
   兜底。两处都幂等。Motor 的 client.close() 是同步方法，不需要 await。
"""

import asyncio
import atexit
import sys
import time
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.core.db.client")

# ═══════════════════════════════════════════════════════════════════
# 模块级状态
# ═══════════════════════════════════════════════════════════════════

# MongoDB 文档就是 dict；用 alias 避免每处都写长串
type DocT = dict[str, Any]
type ClientT = AsyncIOMotorClient[DocT]
type DatabaseT = AsyncIOMotorDatabase[DocT]

_client: ClientT | None = None
_db: DatabaseT | None = None

# asyncio.Lock 模块级创建：Python 3.10+ 懒绑定 loop，安全。
# 所有 init/close 的复合操作在这把锁下串行化。
# 注意：atexit 的同步关闭不走这把锁（见 _atexit_close）。
_init_lock = asyncio.Lock()

_last_fail_time: float = 0.0
_FAIL_COOLDOWN = 10.0
timeout = _hub.dev.MONGO_TIMEOUT_MS


async def init_db() -> None:
    """异步初始化 MongoDB 连接，创建索引。

    ⚠️ 必须在 uvicorn 的 event loop 内首次调用（懒初始化满足）。
    ⚠️ 幂等 + 协程安全：快路径无锁读，慢路径双检。
    """
    global _client, _db
    if _client is not None:
        return
    async with _init_lock:
        if _client is not None:
            return

        new_client: ClientT | None = None
        try:
            new_client = AsyncIOMotorClient(
                host=_hub.dev.MONGO_HOST,
                port=_hub.dev.MONGO_PORT,
                maxPoolSize=20,
                minPoolSize=0,
                serverSelectionTimeoutMS=timeout,
                connectTimeoutMS=timeout,
            )
            new_db = new_client[_hub.dev.MONGO_DB]
            await new_db.command("ping")  # ← 首次 await，绑定当前 loop
            await _create_indexes_on(new_db)

            _client = new_client
            _db = new_db
            logger.info(
                "[DB] MongoDB 连接成功 (%s:%d/%s)",
                _hub.dev.MONGO_HOST,
                _hub.dev.MONGO_PORT,
                _hub.dev.MONGO_DB,
            )
        except Exception as e:
            if new_client is not None:
                try:
                    new_client.close()  # Motor 的 close 是同步的
                except Exception:
                    pass
            logger.error("[DB] ❌ MongoDB 连接失败: %s", e)
            raise


async def _create_indexes_on(db: DatabaseT) -> None:
    await db["tasks"].create_indexes(
        [
            IndexModel([("task_id", ASCENDING)], unique=True),
            IndexModel([("created_at", DESCENDING)]),
            IndexModel([("status", ASCENDING)]),
        ]
    )
    await db["plans"].create_indexes(
        [
            IndexModel([("plan_id", ASCENDING)], unique=True),
            IndexModel([("created_at", DESCENDING)]),
        ]
    )


async def get_db() -> DatabaseT:
    """异步获取 db 句柄。失败冷却保留，防止雪崩。"""
    global _last_fail_time

    if _client is None:
        if time.time() - _last_fail_time < _FAIL_COOLDOWN:
            raise RuntimeError("[DB] MongoDB 最近连接失败，冷却中")
        try:
            await init_db()
        except Exception:
            _last_fail_time = time.time()
            raise
    if _db is None:
        raise RuntimeError("[DB] 数据库未初始化")
    return _db


async def close_db() -> None:
    """显式关闭连接（供 FastAPI shutdown 事件 await）。幂等。"""
    global _client, _db
    async with _init_lock:
        if _client is None:
            return
        try:
            _client.close()  # 同步方法，不 await
            logger.info("[DB] MongoDB 连接已关闭")
        except Exception as e:
            logger.warning("[DB] 关闭连接失败: %s", e)
        _client = None
        _db = None


def _atexit_close() -> None:
    """同步关闭，供 atexit 兜底。绕过 asyncio.Lock（进程退出无并发）。"""
    global _client, _db
    if _client is None:
        return
    try:
        _client.close()
        logger.info("[DB] MongoDB 连接已关闭 (atexit)")
    except Exception as e:
        logger.warning("[DB] atexit 关闭失败: %s", e)
    _client = None
    _db = None


# atexit 兜底：防止 FastAPI shutdown 未触发（进程被 kill -9 之外的异常路径）
if not getattr(sys, "_task_planner_db_atexit_registered", False):
    atexit.register(_atexit_close)
    sys._task_planner_db_atexit_registered = True


__all__ = ["init_db", "get_db", "close_db"]
