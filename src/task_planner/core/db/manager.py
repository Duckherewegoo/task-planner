"""manager.py — DBManager 兼容类（历史包袱，保留）"""
from __future__ import annotations

from typing import Any

from .tasks import get_recent_tasks, list_tasks


class DBManager:
    async def get_recent_tasks(
        self, limit: int = 50, rid_tag: str = "query",
    ) -> list[str]:
        return await get_recent_tasks(limit, rid_tag)

    async def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        return await list_tasks(limit)


db_manager = DBManager()


__all__ = ["DBManager", "db_manager"]
