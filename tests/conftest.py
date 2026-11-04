"""
conftest.py — pytest 全局配置

- 默认全局走 Mock，防止 CI 跑爆 Token / 报 API 错误
- 需要真实 LLM 的测试，通过命令行参数 --use-real-llm 或 marker 动态切换
"""

from __future__ import annotations

import os
import pytest

# 异步 Client 清理，防止 Event loop closed
from task_planner.infrastructure.llm.client import aclose_llm_client

# 默认强制 Mock
os.environ.setdefault("USE_MOCK_LLM", "true") 
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DASHSCOPE_API_KEY", "")  # 空 key 仅作占位，Mock 模式下不请求网络
os.environ.setdefault("CHECKPOINTER_TYPE", "memory")

# 动态读取命令行参数（用于评测真实能力）
def pytest_addoption(parser):
    parser.addoption(
        "--use-real-llm", action="store_true", default=False,
        help="运行真实 LLM 请求（默认为 Mock，防污染、防超支）"
    )

def pytest_configure(config):
    # 如果命令行传了 --use-real-llm，就覆盖环境变量
    if config.getoption("--use-real-llm"):
        os.environ["USE_MOCK_LLM"] = "false"
        # 这里可以安全地加载 .env 中的真实 key
        from dotenv import load_dotenv
        load_dotenv()
        print("\n⚠️ [WARNING] 已启用真实 LLM，注意控制并发和 Token 消耗！")


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
