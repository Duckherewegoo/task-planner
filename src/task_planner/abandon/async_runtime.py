"""async_runtime.py — 进程级常驻 event loop

⚠️ 为什么需要它：
   Dash 4.x WSGI backend 用 asyncio.run() 驱动每个 async 回调——
   每次回调一个新 loop，回调返回即毁。在回调里 asyncio.create_task
   的长期任务，会在回调返回后立即被取消（实测：Cleaner 清理协程
   与 Agent 任务在同秒内先后被取消）。

   本模块提供进程级常驻 loop（跑在独立 daemon 线程），
   供 stream/launcher、stream/cleaner 等"需要跨回调存活"的
   异步任务提交。

⚠️ 关键原则：
   所有 asyncio 原语（Lock / Event / Queue）和 create_task 必须在
   本 loop 上首次执行。跨 loop 使用会抛
   "bound to a different event loop"。

   因此调用方应将"构造 + create_task"整体包进一个协程，通过
   submit() 提交——而不是在 Dash 回调 loop 上构造对象后再提交。
"""

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def get_loop() -> asyncio.AbstractEventLoop:
    """获取（或惰性创建）后台常驻 loop。线程安全。"""
    global _loop, _thread
    if _loop is not None:
        return _loop
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            _thread = threading.Thread(
                target=_loop.run_forever,
                name="task-planner-async-runtime",
                daemon=True,
            )
            _thread.start()
            logger.info(
                "[AsyncRuntime] 后台 event loop 已启动 | thread=%s",
                _thread.name,
            )
    return _loop


def submit(coro: Coroutine[Any, Any, Any]) -> Future:
    """
    从任意线程提交协程到常驻 loop。返回 concurrent.futures.Future。

    在 async 上下文中 await 结果：
        await asyncio.wrap_future(submit(coro))
    """
    return asyncio.run_coroutine_threadsafe(coro, get_loop())


def shutdown() -> None:
    """进程退出时优雅关闭。由 atexit 或 SHUTDOWN 调用。"""
    global _loop, _thread
    if _loop is None:
        return
    logger.info("[AsyncRuntime] 正在关闭后台 loop...")
    _loop.call_soon_threadsafe(_loop.stop)
    if _thread is not None:
        _thread.join(timeout=5)
    _loop.close()
    _loop = None
    _thread = None
    logger.info("[AsyncRuntime] 后台 loop 已关闭")


__all__ = ["get_loop", "submit", "shutdown"]
