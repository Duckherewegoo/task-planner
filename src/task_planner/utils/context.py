"""
context.py — 异步上下文传递（取消信号）
===========================================
在协程间安全传递 asyncio.Event 取消信号。
"""
import asyncio
import contextvars
from contextlib import contextmanager
from typing import Optional

# 当前上下文的取消事件（None 表示无取消信号）
cancel_event_var: contextvars.ContextVar[Optional[asyncio.Event]] = contextvars.ContextVar(
    "cancel_event", default=None
)


@contextmanager
def cancel_scope(event: asyncio.Event):
    """
    上下文管理器：在代码块执行期间将 event 绑定到当前上下文。

    用法:
        with cancel_scope(cancel_event):
            await some_async_function()   # 内部可通过 cancel_event_var.get() 获取
    """
    token = cancel_event_var.set(event)
    try:
        yield event
    finally:
        cancel_event_var.reset(token)
