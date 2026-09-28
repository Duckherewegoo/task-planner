"""
logger_setup.py — 异步友好的日志系统（生产终极版 v2）
═══════════════════════════════════════════════════════════════════
设计原则：
  ✅ 非阻塞写入：通过 QueueHandler + QueueListener 将 I/O 操作移至后台线程
  ✅ 协程安全：使用 ContextVar 自动注入 req_id，支持 asyncio 上下文
  ✅ 可靠稳定：队列积压时提供背压控制，防止内存泄漏
  ✅ 灵活扩展：可轻松接入远程日志、结构化日志等

Changelog:
  ✅ P0-1：只对根 logger（"task_planner"）挂 handler，
           子 logger 用 propagate=True 向上冒泡，
           不再每个模块创建一个后台线程。
  ✅ P0-2：ReqIdFilter 只在 record 没有 req_id 时才补默认值，
           不再覆盖主线程注入的 req_id（修复 [req=-] 问题）。
  ✅ P0-3：set_req_id 的 ContextVar 语义在文档中说明；
           提供 propagate_req_id() 辅助函数用于跨 Task 场景。
  ✅ P1-1：移除未使用的 _logger_initialized。
  ✅ P1-2：保留 root logger 的 WARNING 级别兜底，
           第三方库日志仍可见（但默认过滤 WARNING 以下）。
  ✅ P1-3：shutdown_logging 用 print 记录（listener 已停）。
  ✅ P1-4：显式确保 task_planner.* 相关 logger 都是 ReqIdLogger。
  ✅ P1-5：QueueHandler 队列满时记录 stderr 警告，不再静默丢弃。
"""

import logging
import logging.handlers
import os
import sys
import uuid
import queue
import threading
import contextvars
from contextvars import ContextVar
from typing import Optional, Callable


# ================================================================
#  0. 从 config 导入配置
# ================================================================
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
    """
    设置当前协程/线程的 req_id。

    ⚠️ ContextVar 是 Task-local 的：在 asyncio.create_task / asyncio.gather
       创建的子协程里，默认读不到父协程设置的 req_id。
       如需跨 Task 传播，请用 propagate_req_id()。
    """
    rid = rid or _make_req_id()
    _req_id_var.set(rid)
    return rid


def get_req_id() -> str:
    """获取当前上下文的 req_id"""
    return _req_id_var.get()


def _make_req_id() -> str:
    """生成 8 字符短请求 ID"""
    return uuid.uuid4().hex[:8]


def propagate_req_id() -> contextvars.Context:
    """
    复制当前上下文的 Context 对象。
    在创建子 Task 时用它包一层，让子 Task 也读得到 req_id。

    用法：
        ctx = propagate_req_id()
        await asyncio.gather(
            asyncio.create_task(ctx.run(node_a)),
            asyncio.create_task(ctx.run(node_b)),
        )
    """
    return contextvars.copy_context()


# ================================================================
#  2. 自定义 Logger & Filter
# ================================================================
class ReqIdFilter(logging.Filter):
    """
    ✅ P0-2 修复：
      原实现在后台线程执行 filter 时无条件覆盖 record.req_id，
      导致主线程 makeRecord 阶段注入的 req_id 被覆盖为 ContextVar 的 default "-"。

      现在：只有 record 没带 req_id 时才补默认值。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "req_id", None):
            record.req_id = _req_id_var.get()
        return True


class ReqIdLogger(logging.Logger):
    """
    自定义 Logger：在 makeRecord 阶段注入 req_id。
    这是 req_id 注入的**主要**路径（在调用方线程执行，ContextVar 值正确）。
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
            name, level, fn, lno, msg, args, exc_info,
            func=func, extra=extra, sinfo=sinfo,
        )


# ================================================================
#  3. 日志系统初始化（幂等，线程安全）
# ================================================================
_init_lock = threading.Lock()
_initialized = False
_queue_listener: Optional[logging.handlers.QueueListener] = None
_log_queue: Optional[queue.Queue] = None


# 根 logger 名（所有子模块都是它的子 logger）
_ROOT_LOGGER_NAME = "task_planner"


class _SafeQueueHandler(logging.handlers.QueueHandler):
    """
    ✅ P1-5 修复：队列满时记录 stderr 警告，不再静默丢弃。
    """

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            # 直接写 stderr，避免递归调用 logger
            sys.stderr.write(
                f"[logger_setup] 日志队列已满，丢弃日志: {record.name} {record.getMessage()[:80]}\n"
            )


def setup_logger(name: str = _ROOT_LOGGER_NAME) -> ReqIdLogger:
    """
    获取或创建 logger（幂等）。

    ✅ P0-1 修复：
      只在根 logger（"task_planner"）上挂 handler 和 QueueListener。
      其他 name（如 "task_planner.db"）作为子 logger，propagate=True 向上冒泡。
    """
    global _initialized, _queue_listener, _log_queue

    # 确保 logger 类已注册（仅需一次）
    logging.setLoggerClass(ReqIdLogger)

    # 首次调用时初始化根 logger
    if not _initialized:
        with _init_lock:
            if not _initialized:
                _init_root_logger()
                _initialized = True

    # 返回请求的 logger（可能不是根 logger）
    logger = logging.getLogger(name)
    return logger  # type: ignore


def _init_root_logger() -> None:
    """
    初始化根 logger 的 handlers + QueueListener（只做一次）。
    """
    global _queue_listener, _log_queue

    root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
    root_logger.setLevel(logging.DEBUG)   # 全局最低级别
    root_logger.propagate = False         # 不再向上（Python root）传播

    # ── 格式 ──
    fmt = (
        "%(asctime)s | %(levelname)-7s | %(name)s [%(filename)s:%(lineno)d] "
        "| [req=%(req_id)s] %(message)s"
    )
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # ── 目标 Handler（实际执行 I/O） ──
    console_level = logging.DEBUG if DEBUG else logging.WARNING
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ReqIdFilter())

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
    file_handler.addFilter(ReqIdFilter())

    # ── 队列 + QueueHandler（主线程侧） ──
    _log_queue = queue.Queue(maxsize=10000)
    queue_handler = _SafeQueueHandler(_log_queue)
    queue_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(queue_handler)

    # ── QueueListener（后台线程侧） ──
    _queue_listener = logging.handlers.QueueListener(
        _log_queue,
        console_handler,
        file_handler,
        respect_handler_level=True,
    )
    _queue_listener.start()
    root_logger._queue_listener = _queue_listener  # type: ignore

    # ✅ P1-2 修复：让第三方库的 WARNING+ 也能看到（比如 pymongo 的告警）
    #    但注意：第三方库日志不带 req_id（它们不经过 ReqIdLogger）
    logging.getLogger().setLevel(logging.WARNING)
    if not logging.getLogger().handlers:
        # 只在没有 root handler 时挂一个最小 console
        root_console = logging.StreamHandler()
        root_console.setLevel(logging.WARNING)
        root_console.setFormatter(formatter)
        logging.getLogger().addHandler(root_console)

    # 打印启动信息（这条日志现在能正确带上 req_id）
    root_logger.debug(
        "QueueListener 已启动 (队列容量=%d, 控制台级别=%s, 文件级别=DEBUG, 根logger=%s)",
        _log_queue.maxsize,
        logging.getLevelName(console_level),
        _ROOT_LOGGER_NAME,
    )


# ================================================================
#  4. 便捷函数
# ================================================================
def get_logger(name: str = _ROOT_LOGGER_NAME) -> ReqIdLogger:
    """
    获取 logger（推荐使用该函数替代直接 logging.getLogger）。
    内部保证日志系统已初始化。
    """
    return setup_logger(name)


def shutdown_logging(timeout: float = 2.0) -> None:
    """
    优雅关闭日志系统：等待队列清空后停止 listener。

    ✅ P1-3 修复：用 print 记录（listener 已停，logger 写入会失败）。
    """
    global _queue_listener, _initialized
    if _queue_listener is not None:
        try:
            _queue_listener.stop()
        except Exception:
            pass
        _queue_listener = None
    _initialized = False
    print("[logger_setup] 📦 QueueListener 已关闭，所有日志已写入", file=sys.stderr)
