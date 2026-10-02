"""core.py — 一次 LLM 调用的完整生命周期（重试 / 超时 / 流式 / 取消）"""
from __future__ import annotations

import asyncio
import random
from typing import (
    Any,
    Optional,
)

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

from .client import (
    _get_semaphore,
    get_llm_client,
)  # noqa: F401 (semaphore 转发)
from .errors import (
    LLMCancelledError,
    LLMClientError,
    LLMResponseError,
    LLMTimeoutError,
)

logger = get_logger(__name__)


async def async_call_llm(
    model: str,
    prompt: str,
    enable_thinking: bool,
    req_id: str,
    timeout: Optional[float] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> str:
    """
    异步统一 LLM 调用入口。

    - asyncio.timeout 控制超时
    - asyncio.Event 检查取消信号
    - 支持流式调用（enable_thinking=True 且 cancel_event 存在时）
    - 指数退避 + full jitter 重试
    - 非瞬态错误（4xx / 认证）不重试
    - 所有异常统一转换为 LLMClientError 体系
    """
    client = await get_llm_client()
    if client is None:
        raise LLMClientError("LLM 客户端未初始化，请检查 API Key", req_id)

    if timeout is None:
        timeout = _hub.dev.LLM_NODE_TIMEOUT if enable_thinking else _hub.dev.LLM_TIMEOUT

    last_error: Optional[Exception] = None

    for attempt in range(1, _hub.dev.LLM_MAX_RETRIES + 1):
        # 进入前检查取消
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("任务已取消，停止 LLM 调用", req_id)

        try:
            async with asyncio.timeout(timeout):
                logger.debug(
                    "[LLMClient] 调用模型 %s (第%d次, timeout=%ss, thinking=%s, req=%s)",
                    model, attempt, timeout, enable_thinking, req_id,
                )

                extra_body: Optional[dict[str, Any]] = None
                if enable_thinking:
                    extra_body = {
                        "enable_thinking": True,
                        "thinking_budget": _hub.dev.LLM_THINKING_BUDGET,
                    }

                content = ""

                # ── 流式（带取消支持） ──
                if enable_thinking and cancel_event is not None:
                    stream = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        stream=True,
                        extra_body=extra_body,
                    )
                    reasoning_chunks: list[str] = []
                    content_chunks: list[str] = []
                    try:
                        async for chunk in stream:
                            if cancel_event.is_set():
                                raise LLMCancelledError(
                                    "任务已取消，停止 LLM 流式读取", req_id
                                )
                            if not chunk.choices:
                                continue
                            delta = chunk.choices[0].delta
                            r = getattr(delta, "reasoning_content", None)
                            c = getattr(delta, "content", None)
                            if r:
                                reasoning_chunks.append(r)
                            if c:
                                content_chunks.append(c)
                    finally:
                        if hasattr(stream, "close"):
                            try:
                                await stream.close()
                            except Exception:
                                pass

                    content = (
                        "".join(reasoning_chunks) + "".join(content_chunks)
                    ).strip()

                # ── 非流式 ──
                else:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False,
                        extra_body=extra_body,
                    )
                    msg = response.choices[0].message
                    text = getattr(msg, "content", None) or ""
                    reasoning = getattr(msg, "reasoning_content", None) or ""
                    if enable_thinking:
                        content = (reasoning + text).strip()
                    else:
                        content = text.strip()

                if not content:
                    raise LLMResponseError("API 返回内容为空", req_id)

                logger.info(
                    "[LLMClient] ✅ %s 成功 (attempt=%d, req=%s)",
                    model, attempt, req_id,
                )
                return content

        # ── 异常分级：唯一一个统一的 except 链 ──
        except LLMCancelledError:
            raise

        except LLMResponseError:
            raise

        except (asyncio.TimeoutError, TimeoutError):
            last_error = LLMTimeoutError(f"请求超时 ({timeout}s)", req_id)

        except Exception as e:
            err_str = str(e).lower()
            err_name = type(e).__name__

            # 非瞬态错误：认证 / 参数 / 权限，不重试
            non_retryable = (
                "401" in err_str
                or "403" in err_str
                or "400" in err_str
                or "authentication" in err_str
                or "invalid_api_key" in err_str
                or "permission" in err_str
            )
            if non_retryable:
                logger.error(
                    "[LLMClient] ❌ 非瞬态错误, 不重试: %s: %s (req=%s)",
                    err_name, e, req_id,
                )
                raise LLMClientError(f"LLM 调用失败(不可重试): {e}", req_id) from e

            last_error = e

        # ── 退避 + 重试 ──
        base_wait = _hub.dev.LLM_RETRY_BACKOFF ** attempt
        wait = random.uniform(0, base_wait)

        logger.warning(
            "[LLMClient] ⚠️ %s 失败 (attempt=%d/%d, wait=%.1fs, err=%s, req=%s)",
            model, attempt, _hub.dev.LLM_MAX_RETRIES, wait, last_error, req_id,
        )

        if attempt < _hub.dev.LLM_MAX_RETRIES:
            if cancel_event and cancel_event.is_set():
                raise LLMCancelledError("任务已取消，停止重试", req_id) from last_error
            await asyncio.sleep(wait)
        else:
            logger.error(
                "[LLMClient] ❌ %s 最终失败: %s (req=%s)", model, last_error, req_id
            )
            if isinstance(last_error, LLMTimeoutError):
                raise last_error from None
            raise LLMClientError(
                f"LLM 调用最终失败: {last_error}", req_id
            ) from last_error

    # 理论不可达
    raise LLMClientError("LLM 调用最终失败", req_id)


__all__ = ["async_call_llm", "_get_semaphore"]
