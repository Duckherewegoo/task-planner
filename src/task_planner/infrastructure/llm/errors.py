"""errors.py — LLM 异常体系"""
from __future__ import annotations


class LLMClientError(Exception):
    """LLM 客户端基础异常"""

    def __init__(self, message: str, req_id: str | None = None):
        self.req_id = req_id
        super().__init__(f"[req={req_id}] {message}" if req_id else message)


class LLMTimeoutError(LLMClientError):
    """LLM 调用超时"""
    pass


class LLMCancelledError(LLMClientError):
    """LLM 调用被用户取消"""
    pass


class LLMResponseError(LLMClientError):
    """LLM 返回内容异常（空响应、JSON 解析失败、格式不符等）"""
    pass


__all__ = [
    "LLMClientError",
    "LLMTimeoutError",
    "LLMCancelledError",
    "LLMResponseError",
]
