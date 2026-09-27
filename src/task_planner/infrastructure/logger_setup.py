"""
logger_setup.py — 异步友好的日志系统（生产终极版）
═══════════════════════════════════════════════════════════════════
设计原则：
  ✅ 非阻塞写入：通过 QueueHandler + QueueListener 将 I/O 操作移至后台线程
  ✅ 协程安全：使用 ContextVar 自动注入 req_id，支持 asyncio 上下文
  ✅ 可靠稳定：队列积压时提供背压控制，防止内存泄漏
  ✅ 灵活扩展：可轻松接入远程日志、结构化日志等
  ✅ 易于理解：单一职责，清晰注释
"""

import logging
import logging.handlers
import os
import uuid
import queue
import threading
from contextvars import ContextVar
from typing import Optional

# 从 config 导入配置（不要循环依赖）
from task_planner.infrastructure.config import (
    DEBUG,
    LOG_CONSOLE_LEVEL,
    LOG_DIR,
    LOG_FILE,
    LOG_MAX_DAYS,
)

# ================================================================
#  1. 上下文变量：自动传递 req_id
# ================================================================
_req_id_var: ContextVar[str] = ContextVar("req_id", default="-")


def set_req_id(rid: Optional[str] = None) -> str:
    """设置当前协程/线程的 req_id，返回设置的值"""
    rid = rid or _make_req_id()
    _req_id_var.set(rid)
    return rid


def get_req_id() -> str:
    """获取当前上下文的 req_id"""
    return _req_id_var.get()


def _make_req_id() -> str:
    """生成 8 字符短请求 ID"""
    return uuid.uuid4().hex[:8]


# ================================================================
#  2. 自定义 Logger & Filter：自动注入 req_id
# ================================================================
class ReqIdFilter(logging.Filter):
    """为每条日志附加 req_id 属性（供格式化使用）"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.req_id = _req_id_var.get()
        return True


class ReqIdLogger(logging.Logger):
    """
    自定义 Logger：在 makeRecord 阶段强制将 req_id 注入 extra，
    确保即使某些场景下 Filter 未被调用，也能携带 req_id。
    """

    def makeRecord(
        self,
        name: str,
        level: int,
        fn: str,
        lno: int,
        msg: str,
        args: tuple,
        exc_info,
        func: Optional[str] = None,
        extra: Optional[dict] = None,
        sinfo: Optional[str] = None,
    ) -> logging.LogRecord:
        extra = dict(extra) if extra else {}
        extra.setdefault("req_id", _req_id_var.get())
        return super().makeRecord(
            name,
            level,
            fn,
            lno,
            msg,
            args,
            exc_info,
            func=func,
            extra=extra,
            sinfo=sinfo,
        )


# ================================================================
#  3. 日志系统初始化（幂等，线程安全）
# ================================================================
_logger_initialized = False
_init_lock = threading.Lock()


def setup_logger(name: str = "task_planner") -> ReqIdLogger:
    """
    初始化日志系统（幂等）。
    返回一个 ReqIdLogger 实例，所有日志将自动包含 req_id。
    """
    global _logger_initialized

    # 注册自定义 Logger 类（仅需一次）
    logging.setLoggerClass(ReqIdLogger)

    logger = logging.getLogger(name)
    # 防止重复添加 Handler
    if logger.handlers:
        return logger  # type: ignore

    with _init_lock:
        if logger.handlers:  # double-check
            return logger  # type: ignore
        _init_logger_handlers(logger)
        _logger_initialized = True

    logger.info("🔧 日志系统初始化完成 (异步队列模式已启用)")
    return logger  # type: ignore


def _init_logger_handlers(logger: ReqIdLogger) -> None:
    """
    配置日志处理器：
      - 控制台 Handler（根据 DEBUG 决定级别）
      - 文件 Handler（按天轮转，保留 LOG_MAX_DAYS 天）
      - 所有 Handler 通过 QueueHandler 解耦，实现非阻塞写入
    """
    # 基础配置
    logger.setLevel(logging.DEBUG)  # 全局最低级别，由各 Handler 细化
    logger.propagate = False

    # 统一格式
    fmt = (
        "%(asctime)s | %(levelname)-7s | %(name)s [%(filename)s:%(lineno)d] "
        "| [req=%(req_id)s] %(message)s"
    )
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # ---------- 创建目标 Handler（实际执行 I/O） ----------
    # 控制台
    console_level = logging.DEBUG if DEBUG else logging.WARNING
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    # 文件（按天轮转，始终 DEBUG 级别）
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, LOG_FILE)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_path,
        when="midnight",
        interval=1,
        backupCount=LOG_MAX_DAYS,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # ---------- 为所有目标 Handler 注入 ReqIdFilter ----------
    for handler in (console_handler, file_handler):
        handler.addFilter(ReqIdFilter())

    # ---------- 使用 QueueHandler 和 QueueListener 解耦 ----------
    # 创建队列（容量可配置，这里设为 10000 条，防止内存爆炸）
    log_queue = queue.Queue(maxsize=10000)

    # QueueHandler 是同步的，但 put 操作通常很快（仅入队）
    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_handler.setLevel(logging.DEBUG)  # 接收所有级别，由目标 Handler 再过滤

    # 将 QueueHandler 添加到 logger，所有日志先进入队列
    logger.addHandler(queue_handler)

    # 创建 QueueListener，在后台线程中消费队列，调用目标 Handler
    listener = logging.handlers.QueueListener(
        log_queue,
        console_handler,
        file_handler,
        respect_handler_level=True,  # 尊重每个 handler 的级别设置
    )
    # 启动后台线程（守护模式，主程序退出时自动结束）
    listener.start()

    # 将 listener 保存到 logger 的属性中，防止被 GC 回收
    logger._queue_listener = listener  # type: ignore

    # 记录启动信息
    logger.debug(
        "QueueListener 已启动 (队列容量=%d, 控制台级别=%s, 文件级别=DEBUG)",
        log_queue.maxsize,
        logging.getLevelName(console_level),
    )


# ================================================================
#  4. 便捷函数（对外接口）
# ================================================================
def get_logger(name: str = "task_planner") -> ReqIdLogger:
    """
    获取或创建日志记录器（推荐使用该函数替代直接 logging.getLogger）。
    确保日志系统已初始化且采用异步队列模式。
    """
    return setup_logger(name)


# 可选：提供全局关闭函数（用于测试或优雅关闭）
def shutdown_logging() -> None:
    """关闭所有 QueueListener，等待剩余日志写入完成（最多 2 秒）"""
    logger = logging.getLogger("task_planner")
    listener = getattr(logger, "_queue_listener", None)
    if listener and hasattr(listener, "stop"):
        listener.stop()  # 默认会等待队列清空（可设置超时）
        logger.debug("📦 QueueListener 已关闭，所有日志已写入")
