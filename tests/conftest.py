"""
conftest.py — pytest 全局配置

- 设置测试环境变量（避免触发真实 LLM 客户端初始化）
- 提供共用 fixture
"""
from __future__ import annotations

import os
import sys

import pytest

# 在任何 task_planner 模块被 import 之前，先固定测试环境
#    防止 config.py 读到真实 .env，导致 USE_MOCK_LLM=False
os.environ.setdefault("USE_MOCK_LLM", "true")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DASHSCOPE_API_KEY", "")   # 空 key → 强制 mock
os.environ.setdefault("CHECKPOINTER_TYPE", "memory")

from task_planner.infrastructure.llm.client import aclose_llm_client


@pytest.fixture(autouse=True)
async def _close_llm_client_after_each_test():
    """
    每个测试结束后关闭 LLM 客户端。

    为什么需要：pytest-asyncio 每个用例新建 event loop。
    如果某测试真的调了 LLM，client 会绑到该用例的 loop；
    下个用例换新 loop 时访问同一 client 会抛
    "Event loop is closed"。

    加上这个 fixture 后，跨用例的 loop 复用被切断。
    """
    yield
    await aclose_llm_client()

@pytest.fixture
def sample_intent_plan() -> dict:
    """Mock 模式下的 intent 输出样例"""
    return {
        "needs_planning": True,
        "category": "cooking",
        "summary": "制作番茄炒蛋",
        "complexity": "simple",
    }


@pytest.fixture
def sample_plan() -> dict:
    """Mock 模式下的 plan 输出样例"""
    return {
        "task_name": "番茄炒蛋",
        "description": "制作家常番茄炒蛋",
        "nodes": [
            {"id": 1, "name": "准备食材", "details": "洗切西红柿和鸡蛋"},
            {"id": 2, "name": "炒鸡蛋", "details": "热油炒散鸡蛋盛出"},
        ],
        "edges": [{"from": 1, "to": 2, "label": "食材就绪"}],
    }
