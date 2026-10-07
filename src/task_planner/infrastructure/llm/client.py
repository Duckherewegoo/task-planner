"""client.py — AsyncOpenAI 客户端单例 + 并发信号量"""
from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)

# 客户端与信号量（惰性，绑定当前事件循环）
_client: AsyncOpenAI | None = None
_dashscope_available = False
_client_lock: asyncio.Lock | None = None
_semaphore: asyncio.Semaphore | None = None


def _get_client_lock() -> asyncio.Lock:
    """惰性创建协程锁（首次调用时绑定当前事件循环）"""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


def get_semaphore() -> asyncio.Semaphore:
    """
    惰性创建并发信号量（首次调用时绑定当前事件循环）。
    由 `llm/api.py` 和 `llm/core.py` 共用。
    """
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_hub.dev.LLM_MAX_CONCURRENT)
    return _semaphore

async def get_llm_client() -> AsyncOpenAI | None:
    """异步懒加载 AsyncOpenAI 客户端（协程安全）"""
    global _client, _dashscope_available
    if _client is not None:
        return _client

    async with _get_client_lock():
        # double-check
        if _client is not None:
            return _client

        if _hub.dev.USE_MOCK_LLM or not _hub.dev.DASHSCOPE_API_KEY:
            logger.info("[LLMClient] 启用 Mock 模式")
            return None

        try:
            base_url = (
                _hub.dev.DASHSCOPE_BASE_URL
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            import httpx

            timeout = httpx.Timeout(_hub.dev.LLM_TIMEOUT, connect=5.0)
            http_client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=_hub.dev.LLM_MAX_CONCURRENT * 2
                ),
            )
            _client = AsyncOpenAI(
                api_key=_hub.dev.DASHSCOPE_API_KEY,
                base_url=base_url,
                http_client=http_client,
            )
            _dashscope_available = True
            logger.info(
                "[LLMClient] AsyncOpenAI 客户端就绪 (base=%s, max_conn=%d)",
                base_url,
                _hub.dev.LLM_MAX_CONCURRENT,
            )
        except ImportError:
            logger.error("[LLMClient] openai 未安装: pip install openai")
            return None

    return _client


__all__ = ["get_llm_client", "get_semaphore"]
