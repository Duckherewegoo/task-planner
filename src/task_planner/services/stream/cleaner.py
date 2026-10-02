"""cleaner.py — StreamStateCleaner：全局状态池 + TTL 清理"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

from .state import TaskStreamState

logger = get_logger("task_planner.stream")


class StreamStateCleaner:
    """
    TaskStreamState 内存垃圾回收器（异步版）
    - 后台协程：asyncio.create_task 启动
    - 惰性启动：首次注册任务时才启动
    - TTL 兜底：防止异常未标记 finished 的任务永久驻留
    """

    CLEAN_INTERVAL = 60          # 清理周期 60s
    FINISHED_RETENTION = 300     # 已完成任务额外保留 5min

    def __init__(self) -> None:
        self._states: dict[str, TaskStreamState] = {}
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ── 注册 / 注销 ──
    async def register(self, thread_id: str, state: TaskStreamState) -> None:
        async with self._lock:
            self._states[thread_id] = state
        await self._ensure_started()
        logger.debug(
            "[Cleaner] 注册任务 %s (当前活跃: %d)", thread_id, len(self._states)
        )

    async def unregister(self, thread_id: str) -> None:
        async with self._lock:
            removed = self._states.pop(thread_id, None)
        if removed:
            logger.debug("[Cleaner] 主动移除任务 %s", thread_id)

    async def get(self, thread_id: str) -> Optional[TaskStreamState]:
        async with self._lock:
            return self._states.get(thread_id)

    @property
    def active_count(self) -> int:
        """同步属性，快速读取（不涉及 I/O）"""
        return len(self._states)

    # ── 后台清理 ──
    async def _ensure_started(self) -> None:
        """惰性启动（双重检查锁）"""
        if self._running:
            return
        async with self._lock:
            if self._running:
                return
            self._running = True
            self._task = asyncio.create_task(self._cleanup_loop())
            logger.info(
                "[Cleaner] 清理协程已启动 (interval=%ds)",
                self.CLEAN_INTERVAL,
            )

    async def _cleanup_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.CLEAN_INTERVAL)
                await self._do_cleanup()
            except asyncio.CancelledError:
                logger.info("[Cleaner] 清理协程被取消")
                break
            except Exception as e:
                logger.error("[Cleaner] 清理循环异常: %s", e, exc_info=True)

    async def _do_cleanup(self) -> None:
        now = time.time()
        to_remove: list[str] = []

        async with self._lock:
            for tid, state in self._states.items():
                should_remove = False

                if state.finished:
                    age = state.age_since_finished(now)
                    if age is not None and age > self.FINISHED_RETENTION:
                        should_remove = True
                else:
                    if state.is_expired(now):
                        should_remove = True
                        # ✅ 用公开方法，不访问私有属性
                        logger.warning(
                            "[Cleaner] TTL 超时强制回收: %s (age=%.0fs)",
                            tid, state.age_since_created(now),
                        )

                if should_remove:
                    to_remove.append(tid)

            for tid in to_remove:
                del self._states[tid]

        if to_remove:
            logger.info(
                "[Cleaner] 本轮清理 %d 个任务 (剩余活跃: %d)",
                len(to_remove), len(self._states),
            )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[Cleaner] 已停止")


# 全局单例
state_cleaner = StreamStateCleaner()


__all__ = ["StreamStateCleaner", "state_cleaner"]
