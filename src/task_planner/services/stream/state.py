"""state.py — TaskStreamState：单个流式任务的状态快照 + 节点状态"""

import asyncio
import time
from collections import deque

from task_planner.infrastructure.cog import hub as _hub


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
        self.snapshots: deque[dict] = deque(maxlen=max_snapshots)
        self.latest: dict = {}

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
        self._ttl = ttl if ttl is not None else _hub.dev.SESSION_TTL

        # 后台任务引用（用于取消）
        self._task: asyncio.Task | None = None

    # ── 时间查询 ──
    def is_expired(self, now: float | None = None) -> bool:
        """判断是否超时。

        基准是 `_last_active_at`（最后活动时刻）：
          - running 态：每次 push / set_node_state 更新
          - paused 态：mark_paused 时刻更新
        这样 paused 期间用户思考时间不计入 TTL，避免误清正在等待交互的任务。
        """
        now = now or time.time()
        if self.finished:
            return False
        return now - self._last_active_at > self._ttl

    def age_since_created(self, now: float | None = None) -> float:
        """创建至今秒数（审计用，不受活动影响）"""
        now = now or time.time()
        return now - self._created_at

    def age_since_finished(self, now: float | None = None) -> float | None:
        """完成至今秒数；未完成时返回 None"""
        if self._finished_at is None:
            return None
        now = now or time.time()
        return now - self._finished_at

    def age_since_active(self, now: float | None = None) -> float:
        """最后一次活动至今秒数（用于诊断 / 监控）"""
        now = now or time.time()
        return now - self._last_active_at

    # ── 快照推入 ──
    async def push(self, snapshot: dict) -> None:
        """推入新快照，并同步节点状态。

        若此前处于 paused 状态（用户点 resume 后图重新推进），
        自动清除 paused 标记——图已重新开始动，不再是暂停。
        """
        async with self._lock:
            # 关键字段状态继承，防止中间快照清空
            for key in ("nodes", "edges", "direct_response", "flowchart_html", "task_id"):
                if not snapshot.get(key) and self.latest.get(key):
                    snapshot[key] = self.latest[key]

            self.snapshots.append(snapshot)
            self.latest = snapshot
            await self._sync_node_states(snapshot)

            # 恢复活动：清除 paused，重置活动时间
            self.paused = False
            self.next_nodes = []
            self._paused_at = None
            self._last_active_at = time.time()

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
            self._last_active_at = time.time()

    async def mark_paused(self, next_nodes: list[str]) -> None:
        """标记暂停：图停在 interrupt，等待用户交互。

        与 mark_finished 互斥：
          - mark_paused ：finished 保持 False，session 保留，可 resume
          - mark_finished：finished=True，session 可清理，不可 resume

        调用时机：`stream._worker` 收到 `{"type": "paused"}` snapshot。
        resume 后由 push 自动清除 paused 标记。
        """
        async with self._lock:
            if self.finished:
                # 已结束的任务不允许暂停
                return
            self.paused = True
            self.next_nodes = list(next_nodes)
            self._paused_at = time.time()
            self._last_active_at = self._paused_at

    async def mark_finished(self, error: str | None = None) -> None:
        async with self._lock:
            if not self.finished:
                self.finished = True
                self.paused = False
                self.next_nodes = []
                self.error = error
                self._finished_at = time.time()
                self._last_active_at = self._finished_at

    async def mark_cancelled(self) -> None:
        async with self._lock:
            if not self.finished:
                self.cancelled = True
                self.finished = True
                self.paused = False
                self.next_nodes = []
                self._finished_at = time.time()
                self._last_active_at = self._finished_at

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
