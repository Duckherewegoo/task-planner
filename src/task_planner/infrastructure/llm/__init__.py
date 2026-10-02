"""
llm — LLM 调用封装（拆分版）
═══════════════════════════════════════════════════
对外接口与旧 llm_client.py 完全一致：

    from task_planner.infrastructure.llm import recognize_intent
    from task_planner.infrastructure.llm import recognize_intent  # 兼容

子模块：
  errors        异常体系
  client        AsyncOpenAI 单例 + 信号量
  json_utils    JSON 清洗 / 提取 / 归一化
  validator     意图枚举校验
  mocks         Mock 数据
  core          单次 LLM 调用（重试/超时/流式）
  api           5 个业务接口
"""
from .api import (
    direct_chat,
    execute_node_llm,
    generate_plan,
    recognize_intent,
    refine_node,
)
from .client import get_llm_client
from .core import async_call_llm
from .errors import (
    LLMCancelledError,
    LLMClientError,
    LLMResponseError,
    LLMTimeoutError,
)
from .json_utils import (
    extract_json,
    normalize_llm_output,
)

# 兼容旧名
from task_planner.infrastructure.prompts.loader import render_template as _render_template

__all__ = [
    # 业务接口
    "direct_chat",
    "recognize_intent",
    "generate_plan",
    "refine_node",
    "execute_node_llm",
    # 底层
    "get_llm_client",
    "async_call_llm",
    # 异常
    "LLMClientError",
    "LLMTimeoutError",
    "LLMCancelledError",
    "LLMResponseError",
    # 工具
    "extract_json",
    "normalize_llm_output",
    "_render_template",
]
