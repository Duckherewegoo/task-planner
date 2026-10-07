"""state.py — TaskStreamState：单个流式任务的状态快照 + 节点状态"""
from __future__ import annotations

import asyncio
import time
from collections import deque

from task_planner.infrastructure.cog import hub as _hub


class TaskStreamState:
    """单个流式任务的状态快照 + 节点状态（协程安全）"""

    def __init__(
        self,
        thread_id: str,
        ttl: int | None = None,
        max_snapshots: int = 100,
    ):
        self.thread_id = thread_id

        # 快照环形缓冲
        self.snapshots: deque[dict] = deque(maxlen=max_snapshots)
        self.latest: dict = {}

        # 生命周期
        self.finished: bool = False
        self.cancelled: bool = False
        self.error: str | None = None
        self._created_at: float = time.time()
        self._finished_at: float | None = None

        # 节点用户操作状态
        self.node_states: dict[str, str] = {}

        # 协程锁：保护 latest / node_states / finished
        self._lock = asyncio.Lock()
        # ✅ 每次创建时读最新配置，而非模块级快照
        self._ttl = ttl if ttl is not None else _hub.dev.SESSION_TTL

        # 后台任务引用（用于取消）
        self._task: asyncio.Task | None = None

    # ── 时间查询 ──
    def is_expired(self, now: float | None = None) -> bool:
        """判断是否超时（未完成状态下）"""
        now = now or time.time()
        return not self.finished and (now - self._created_at > self._ttl)

    def age_since_created(self, now: float | None = None) -> float:
        """创建至今秒数（替代直接访问 _created_at）"""
        now = now or time.time()
        return now - self._created_at

    def age_since_finished(self, now: float | None = None) -> float | None:
        """完成至今秒数；未完成时返回 None"""
        if self._finished_at is None:
            return None
        now = now or time.time()
        return now - self._finished_at

    # ── 快照推入 ──
    async def push(self, snapshot: dict) -> None:
        """推入新快照，并同步节点状态"""
        async with self._lock:
            # 关键字段状态继承，防止中间快照清空
            for key in ("nodes", "edges", "direct_response",
                        "flowchart_html", "task_id"):
                if not snapshot.get(key) and self.latest.get(key):
                    snapshot[key] = self.latest[key]

            self.snapshots.append(snapshot)
            self.latest = snapshot
            await self._sync_node_states(snapshot)

    async def _sync_node_states(self, snap: dict) -> None:
        """从 snapshot 的 nodes/node_results 同步状态（已持锁）"""
        nodes = snap.get("nodes") or []
        current_idx = snap.get("current_node_index", 0)
        results = snap.get("node_results") or []

        # 1. 基础 status
        for n in nodes:
            nid = str(n["id"])
            st = int(n.get("status", 0))
            if st == 2 and nid not in self.node_states:
                self.node_states[nid] = "done"
            elif st == 1 and self.node_states.get(nid) != "done":
                self.node_states[nid] = "running"
            elif st == 3:
                self.node_states[nid] = "failed"

        # 2. node_results 优先
        for r in results:
            nid = str(r.get("node_id", ""))
            if not nid:
                continue
            if r.get("success") is True:
                self.node_states[nid] = "done"
            elif r.get("success") is False:
                self.node_states[nid] = "failed"

        # 3. 当前节点标记 running
        if 0 <= current_idx < len(nodes):
            cur_id = str(nodes[current_idx]["id"])
            if self.node_states.get(cur_id) not in ("done", "failed", "skipped"):
                self.node_states[cur_id] = "running"

    # ── 读取 ──
    async def get_latest(self) -> dict:
        """获取最新快照（注入实时 node_states）"""
        async with self._lock:
            if not self.latest:
                return {}
            result = dict(self.latest)
            result["node_states"] = dict(self.node_states)
            return result

    async def get_node_states(self) -> dict[str, str]:
        async with self._lock:
            return dict(self.node_states)

    # ── 写入 ──
    async def set_node_state(self, node_id: str, state: str) -> None:
        async with self._lock:
            self.node_states[node_id] = state

    async def mark_finished(self, error: str | None = None) -> None:
        async with self._lock:
            if not self.finished:
                self.finished = True
                self.error = error
                self._finished_at = time.time()

    async def mark_cancelled(self) -> None:
        async with self._lock:
            if not self.finished:
                self.cancelled = True
                self.finished = True
                self._finished_at = time.time()

    # ── 后台任务管理 ──
    def set_task(self, task: asyncio.Task) -> None:
        self._task = task

    def abort_worker(self) -> None:
        """
        杀 asyncio.Task，停止后台快照推送。
        与 agent.cancel_task 配对使用：先 agent.cancel_task，后 abort_worker。
        """
        if self._task and not self._task.done():
            self._task.cancel()


__all__ = ["TaskStreamState"]
