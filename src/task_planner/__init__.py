"""
task_planner — 智能任务计划规划生成流程图助手 v6.0
"""

__version__ = "6.0.0"

from task_planner.infrastructure.config import (
    DASHSCOPE_API_KEY,
    DEBUG,
    DEFAULT_EXPORT_FORMAT,
    ENABLE_MCP,
    GRADIO_HOST,
    GRADIO_PORT,
    LLM_INTENT_MODEL,
    LLM_MAX_RETRIES,
    LLM_NODE_MODEL,
    LLM_NODE_TIMEOUT,
    LLM_PLANNER_MODEL,
    LLM_RETRY_BACKOFF,
    LLM_TASK_TOTAL_TIMEOUT,
    LLM_TIMEOUT,
    MONGO_DB,
    MONGO_HOST,
    MONGO_PORT,
    RENDER_DIR,
    RENDER_DPI,
    RENDER_FONT,
    RENDER_HEIGHT,
    RENDER_WIDTH,
    STATUS_BORDER,
    STATUS_COLOR,
    STATUS_TEXT,
    SUPPORTED_EXPORT_FORMATS,
    TASK_STATUS,
    USE_MOCK_LLM,
)
from task_planner.infrastructure.logger_setup import get_req_id, set_req_id, setup_logger

# logger 是 setup_logger() 的返回值（单例）
logger = setup_logger("task_planner")

__all__ = [
    "DASHSCOPE_API_KEY",
    "DEBUG",
    "DEFAULT_EXPORT_FORMAT",
    "ENABLE_MCP",
    "GRADIO_HOST",
    "GRADIO_PORT",
    "LLM_INTENT_MODEL",
    "LLM_MAX_RETRIES",
    "LLM_NODE_MODEL",
    "LLM_NODE_TIMEOUT",
    "LLM_PLANNER_MODEL",
    "LLM_RETRY_BACKOFF",
    "LLM_TASK_TOTAL_TIMEOUT",
    "LLM_TIMEOUT",
    "MONGO_DB",
    "MONGO_HOST",
    "MONGO_PORT",
    "RENDER_DIR",
    "RENDER_DPI",
    "RENDER_FONT",
    "RENDER_HEIGHT",
    "RENDER_WIDTH",
    "STATUS_BORDER",
    "STATUS_COLOR",
    "STATUS_TEXT",
    "SUPPORTED_EXPORT_FORMATS",
    "TASK_STATUS",
    "USE_MOCK_LLM",
    "__version__",
    "get_req_id",
    "logger",
    "set_req_id",
    "setup_logger",
]

