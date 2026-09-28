"""
context.py — 异步上下文传递（取消信号）
===========================================
在协程间安全传递 asyncio.Event 取消信号。

Changelog:
  ✅ 新增 get_cancel_event() / is_cancelled() / raise_if_cancelled()
     三个便捷 API，nodes.py 里的 _get_cancel_event 可以删掉了。
  ✅ 新增 bind_cancel_event() 作为 cancel_scope 的语义化别名。
  ✅ 文档说明 ContextVar 的跨 Task 语义（拷贝 vs 共享）。
  ✅ cancel_scope 的 yield 值保持向后兼容（仍返回 event）。
"""
import asyncio
import contextvars
from contextlib import contextmanager
from typing import Optional

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
    若当前上下文已取消，抛 asyncio.CancelledError。

    节点/LLM 调用方在关键路径开头调一次，即可优雅响应取消：
        raise_if_cancelled()
    """
    e = cancel_event_var.get()
    if e is not None and e.is_set():
        raise asyncio.CancelledError("user cancelled")


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
    "cancel_event_var",
    "get_cancel_event",
    "is_cancelled",
    "raise_if_cancelled",
    "cancel_scope",
    "bind_cancel_event",
]
