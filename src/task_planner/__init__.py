"""task_planner — 智能任务计划规划生成流程图助手 v6.0"""

from importlib.metadata import version

__version__ = version("task-planner")

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import (
    STATUS_BORDER,
    STATUS_COLOR,
    STATUS_TEXT,
    SUPPORTED_EXPORT_FORMATS,
    TASK_STATUS,
)
from task_planner.infrastructure.logger_setup import get_req_id, set_req_id, setup_logger


def _safe_dev_get(name: str):
    """从 cog 读 dev 配置；未注册返回 None，不抛。

    目的：让 `import task_planner` 在 cog 未完整配置（如 CI 无 .env）时
    也能成功——避免"import 就炸"的隐式门槛。

    代价：未配置字段的值为 None，而非抛 AttributeError。
    消费方若需要"配了才用"，自行做 `if X is None: ...` 判断。
    """
    try:
        return getattr(_hub.dev, name)
    except AttributeError:
        return None


# ── 顶层导出：cog 字段（未配则为 None，import 不炸）──
DASHSCOPE_API_KEY = _safe_dev_get("DASHSCOPE_API_KEY")
DEBUG = _safe_dev_get("DEBUG")
DEFAULT_EXPORT_FORMAT = _safe_dev_get("DEFAULT_EXPORT_FORMAT")
ENABLE_MCP = _safe_dev_get("ENABLE_MCP")
LLM_INTENT_MODEL = _safe_dev_get("LLM_INTENT_MODEL")
LLM_MAX_RETRIES = _safe_dev_get("LLM_MAX_RETRIES")
LLM_NODE_MODEL = _safe_dev_get("LLM_NODE_MODEL")
LLM_NODE_TIMEOUT = _safe_dev_get("LLM_NODE_TIMEOUT")
LLM_PLANNER_MODEL = _safe_dev_get("LLM_PLANNER_MODEL")
LLM_RETRY_BACKOFF = _safe_dev_get("LLM_RETRY_BACKOFF")
LLM_TASK_TOTAL_TIMEOUT = _safe_dev_get("LLM_TASK_TOTAL_TIMEOUT")
LLM_TIMEOUT = _safe_dev_get("LLM_TIMEOUT")
MONGO_DB = _safe_dev_get("MONGO_DB")
MONGO_HOST = _safe_dev_get("MONGO_HOST")
MONGO_PORT = _safe_dev_get("MONGO_PORT")
RENDER_DIR = _safe_dev_get("RENDER_DIR")
RENDER_DPI = _safe_dev_get("RENDER_DPI")
RENDER_FONT = _safe_dev_get("RENDER_FONT")
RENDER_HEIGHT = _safe_dev_get("RENDER_HEIGHT")
RENDER_WIDTH = _safe_dev_get("RENDER_WIDTH")
USE_MOCK_LLM = _safe_dev_get("USE_MOCK_LLM")


# logger 是 setup_logger() 的返回值（单例）。
# ⚠️ 若 setup_logger 内部读 cog，CI 无 .env 时这里也会炸——同样 try 兜底。
try:
    logger = setup_logger("task_planner")
except Exception:
    import logging as _logging

    logger = _logging.getLogger("task_planner")


__all__ = [
    "DASHSCOPE_API_KEY",
    "DEBUG",
    "DEFAULT_EXPORT_FORMAT",
    "ENABLE_MCP",
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
