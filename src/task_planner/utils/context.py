"""
context.py — 异步上下文传递（取消信号）
═══════════════════════════════════════════════════════════════════════
在协程间安全传递 asyncio.Event 取消信号。

Changelog:
  ── v1 ──
  ✅ get_cancel_event() / is_cancelled() / raise_if_cancelled() 便捷 API
  ✅ bind_cancel_event() 作为 cancel_scope 的语义化别名
  ✅ 文档说明 ContextVar 的跨 Task 语义（拷贝 vs 共享）

  ── v2 ──
  ✅ P1-1：新增 OperationCancelled 异常类。
           raise_if_cancelled() 改为抛 OperationCancelled，
           不再用 asyncio.CancelledError（该异常在 asyncio 生态中
           专用于"外部 task.cancel()"，与"用户主动取消"语义冲突，
           会被 stream/agent 层误捕为外部取消）。
  ✅ P1-2：__all__ 补 OperationCancelled。
"""
from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager
from typing import Optional


# ══════════════════════════════════════════════════
#  异常
# ══════════════════════════════════════════════════
class OperationCancelled(Exception):
    """
    用户/上层主动取消操作。

    与 asyncio.CancelledError 的区别：
      - asyncio.CancelledError：外部 task.cancel() 触发，由 asyncio 内部使用
      - OperationCancelled    ：业务层"用户点了停止"等主动取消

    调用方（节点/LLM 调用）捕获 OperationCancelled 后，应：
      - 清理资源
      - 向上返回取消状态（不重抛 asyncio.CancelledError）
    """


# ══════════════════════════════════════════════════
#  上下文变量
# ══════════════════════════════════════════════════
# 当前上下文的取消事件（None 表示无取消信号）
#
# ⚠️ ContextVar 跨 Task 语义：
#   - asyncio.create_task(coro) 会**拷贝**当前上下文，
#     所以子 Task 能读到父 Task 设置的 cancel_event。
#   - 但子 Task 里 set 的值**不会**传回父 Task。
#   - agent.py 的 run_task_stream 用了 contextvars.copy_context() + ctx.run()
#     来做显式的跨生成器绑定，那是另一套机制，两者不冲突。
cancel_event_var: contextvars.ContextVar[Optional[asyncio.Event]] = (
    contextvars.ContextVar("cancel_event", default=None)
)


# ══════════════════════════════════════════════════
#  读取 API（节点/LLM 调用方使用）
# ══════════════════════════════════════════════════
def get_cancel_event() -> Optional[asyncio.Event]:
    """获取当前上下文的 cancel_event（None 表示无取消信号）。"""
    return cancel_event_var.get()


def is_cancelled() -> bool:
    """当前上下文是否已收到取消信号。"""
    e = cancel_event_var.get()
    return e is not None and e.is_set()


def raise_if_cancelled() -> None:
    """
    若当前上下文已取消，抛 OperationCancelled。

    节点/LLM 调用方在关键路径开头调一次，即可优雅响应取消：
        raise_if_cancelled()

    注意：抛的是 OperationCancelled 而非 asyncio.CancelledError，
          避免被上层误判为"外部 task.cancel()"。
    """
    e = cancel_event_var.get()
    if e is not None and e.is_set():
        raise OperationCancelled("user cancelled")


# ══════════════════════════════════════════════════
#  绑定 API（会话/Agent 调用方使用）
# ══════════════════════════════════════════════════
@contextmanager
def cancel_scope(event: asyncio.Event):
    """
    上下文管理器：在代码块执行期间把 event 绑定到当前上下文。

    用法：
        with cancel_scope(cancel_event):
            await some_async_function()
            # 内部通过 get_cancel_event() / is_cancelled() 感知取消

    ⚠️ 跨 Task 语义：
        - 块内 asyncio.create_task(...) 创建的子 Task 能读到 event
        - 子 Task 内的 set 不会影响本上下文

    退出时会 reset 到绑定前的值（支持嵌套）。
    """
    token = cancel_event_var.set(event)
    try:
        yield event
    finally:
        cancel_event_var.reset(token)


# 语义化别名
bind_cancel_event = cancel_scope


__all__ = [
    "OperationCancelled",
    "cancel_event_var",
    "get_cancel_event",
    "is_cancelled",
    "raise_if_cancelled",
    "cancel_scope",
    "bind_cancel_event",
]
