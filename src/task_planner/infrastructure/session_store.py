# ================================================================
#  infrastructure/session_store.py （新建）
# ================================================================
from typing import Protocol, Optional
from task_planner.infrastructure.config import SESSION_TTL as _SESSION_TTL
import asyncio, time

class SessionStore(Protocol):
    async def get(self, tid: str) -> Optional["TaskSession"]: ...
    async def set(self, tid: str, s: "TaskSession") -> None: ...
    async def pop(self, tid: str) -> Optional["TaskSession"]: ...
    async def cleanup_stale(self, ttl: int) -> int: ...


class InMemorySessionStore:
    def __init__(self):
        self._data: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def get(self, tid):
        async with self._lock:
            e = self._data.get(tid)
            if not e:
                return None
            if time.time() - e["start_time"] > _SESSION_TTL:
                self._data.pop(tid, None)
                return None
            return e["session"]

    async def set(self, tid, s):
        async with self._lock:
            self._data[tid] = {"session": s, "start_time": s.start_time}

    async def pop(self, tid):
        async with self._lock:
            e = self._data.pop(tid, None)
            return e["session"] if e else None

    async def cleanup_stale(self, ttl):
        now = time.time()
        async with self._lock:
            stale = [k for k, v in self._data.items() if now - v["start_time"] > ttl]
            for k in stale:
                self._data.pop(k, None)
        return len(stale)
