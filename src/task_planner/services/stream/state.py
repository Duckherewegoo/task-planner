"""state.py — TaskStreamState：单个流式任务的状态快照 + 节点状态"""

import asyncio
import time
from collections import deque
from typing import Any

from task_planner.infrastructure.cog import hub as _hub

# snapshot 是外部流层 yield 的 dict，字段随业务演进；
# 用明确的 dict[str, Any] 而非裸 dict，消 pyright 的 reportMissingTypeArgument。
type Snapshot = dict[str, Any]


class TaskStreamState:
    """单个流式任务的状态快照 + 节点状态（协程安全）。

    生命周期：
      RUNNING → PAUSED（图停在 interrupt） → RUNNING（resume 后） → FINISHED
                                      └→ CANCELLED

    与 finished 的语义区分：
      - finished=True  ：任务彻底结束（完成/失败/取消），session 可清理，不可 resume
      - paused=True    ：任务暂停在 interrupt，等待用户交互，session 必须保留
      两者互斥：paused 时 finished 必为 False。

    TTL 语义：
      超时判定基于 `_last_active_at`（最后活动时刻），而非 `_created_at`。
      原因：paused 状态下用户可能思考数分钟才点击 resume，
           如果按 created_at 起算，正在等待用户交互的任务会被误判过期清理。
      `_created_at` 保留用于审计（age_since_created）。
    """

    def __init__(
        self,
        thread_id: str,
        ttl: int | None = None,
        max_snapshots: int = 100,
    ):
        self.thread_id = thread_id

        # 快照环形缓冲
        self.snapshots: deque[Snapshot] = deque(maxlen=max_snapshots)
        self.latest: Snapshot = {}

        # 生命周期
        self.finished: bool = False
        self.cancelled: bool = False
        self.paused: bool = False
        self.next_nodes: list[str] = []
        self.error: str | None = None

        # 时间戳
        self._created_at: float = time.time()
        self._last_active_at: float = self._created_at
        self._finished_at: float | None = None
        self._paused_at: float | None = None

        # 节点用户操作状态
        self.node_states: dict[str, str] = {}

        # 协程锁：保护 latest / node_states / finished / paused
        self._lock = asyncio.Lock()

        # 每次创建时读最新配置，而非模块级快照
        # 一个任务的生命周期应该用创建时的 TTL，中途变 TTL 会让 is_expired 行为跳变
        self._ttl = ttl if ttl is not None else _hub.dev.SESSION_TTL

        # 后台任务引用（用于取消）
        self._task: asyncio.Task[None] | None = None

    # ── 时间查询 ──（这些方法没有类型问题，保持原样）

    # ── 快照推入 ──
    async def push(self, snapshot: Snapshot) -> None:
        """推入新快照，并同步节点状态。..."""
        async with self._lock:
            snap: Snapshot = dict(snapshot)
            for key in ("nodes", "edges", "direct_response", "flowchart_html", "task_id"):
                if not snap.get(key) and self.latest.get(key):
                    snap[key] = self.latest[key]

            self.snapshots.append(snap)
            self.latest = snap
            await self._sync_node_states(snap)

            self.paused = False
            self.next_nodes = []
            self._paused_at = None
            self._last_active_at = time.time()

    async def _sync_node_states(self, snap: Snapshot) -> None:
        """从 snapshot 的 nodes/node_results 同步状态（已持锁）"""
        nodes = snap.get("nodes") or []
        current_idx = snap.get("current_node_index", 0)
        results = snap.get("node_results") or []

        for n in nodes:
            nid = str(n["id"])
            st = int(n.get("status", 0))
            if st == 2 and nid not in self.node_states:
                self.node_states[nid] = "done"
            elif st == 1 and self.node_states.get(nid) != "done":
                self.node_states[nid] = "running"
            elif st == 3:
                self.node_states[nid] = "failed"

        for r in results:
            nid = str(r.get("node_id", ""))
            if not nid:
                continue
            if r.get("success") is True:
                self.node_states[nid] = "done"
            elif r.get("success") is False:
                self.node_states[nid] = "failed"

        if 0 <= current_idx < len(nodes):
            cur_id = str(nodes[current_idx]["id"])
            if self.node_states.get(cur_id) not in ("done", "failed", "skipped"):
                self.node_states[cur_id] = "running"

    # ── 读取 ──
    async def get_latest(self) -> Snapshot:
        async with self._lock:
            if not self.latest:
                return {}
            result: Snapshot = dict(self.latest)
            result["node_states"] = dict(self.node_states)
            return result

    async def get_node_states(self) -> dict[str, str]:
        async with self._lock:
            return dict(self.node_states)

    # ── 后台任务管理 ──
    def set_task(self, task: asyncio.Task[None]) -> None:
        self._task = task

    def abort_worker(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
