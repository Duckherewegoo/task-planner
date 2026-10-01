"""client.py — MongoDB 连接生命周期管理（异步，协程安全）"""
from __future__ import annotations

import asyncio
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.db.client")

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None
_db_lock = asyncio.Lock()
_initialized = False


async def init_db() -> None:
    """异步初始化 MongoDB 连接，创建索引"""
    global _client, _db, _initialized
    if _initialized:
        return
    async with _db_lock:
        if _initialized:
            return

        # 先构造本地变量，全部成功后再赋给全局（防半成品泄漏）
        new_client = None
        try:
            new_client = AsyncIOMotorClient(
                host=_hub.dev.MONGO_HOST,
                port=_hub.dev.MONGO_PORT,
                maxPoolSize=100,
                minPoolSize=10,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
            )
            new_db = new_client[_hub.dev.MONGO_DB]
            await new_db.command("ping")
            await _create_indexes_on(new_db)

            _client = new_client
            _db = new_db
            _initialized = True
            logger.info(
                "[DB] ✅ MongoDB 异步连接成功 (%s:%d/%s)",
                _hub.dev.MONGO_HOST, _hub.dev.MONGO_PORT, _hub.dev.MONGO_DB,
            )
        except Exception as e:
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
    """显式关闭连接（用于测试/进程退出）"""
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


__all__ = ["init_db", "get_db", "close_db"]
