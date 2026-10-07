"""
logger_setup.py — 异步友好的日志系统（生产终极版 v3）
═══════════════════════════════════════════════════════════════════
设计原则：
  ✅ 非阻塞写入：QueueHandler + QueueListener 将 I/O 移至后台线程
  ✅ 协程安全：ContextVar 自动注入 req_id，支持 asyncio 上下文
  ✅ 可靠稳定：队列积压背压控制，防止内存泄漏
  ✅ 灵活扩展：可接入远程日志、结构化日志

Changelog:
  ── v1 ──
  ✅ P0-1：只对根 logger（"task_planner"）挂 handler，子 logger 用 propagate
  ✅ P0-2：ReqIdFilter 只在 record 无 req_id 时补默认值，不覆盖主线程注入值
  ✅ P0-3：set_req_id 的 ContextVar 语义说明；提供 propagate_req_id()
  ✅ P1-1：移除未使用的 _logger_initialized
  ✅ P1-2：保留 root logger WARNING 级别兜底，第三方库日志可见
  ✅ P1-3：shutdown_logging 用 print（listener 已停）
  ✅ P1-4：确保 task_planner.* 相关 logger 都是 ReqIdLogger
  ✅ P1-5：QueueHandler 队列满时记录 stderr 警告，不再静默丢弃

  ── v2 ──
  ✅ P1-6：LOG_DIR / LOG_FILE 空值兜底，回退到项目根/logs 与默认文件名
  ✅ P1-7：_SafeQueueHandler.enqueue 对 record.getMessage() 加 try，
           防止格式化失败拖垮 stderr 写入
  ✅ P1-8：shutdown_logging 清理 root_logger 上的 handler，
           避免重新初始化时重复挂 handler 导致日志翻倍
  ✅ P1-9：_project_root 模块级缓存，避免每次初始化重复计算 Path

  ── v3 ──
  ✅ P2-1：set_req_id / get_req_id 增加 None 保护
  ✅ P2-2：propagate_req_id 文档补充与 asyncio.Task 的配合示例
  ✅ P2-3：TimedRotatingFileHandler 增加 delay=True，
           首次写入才创建文件，避免空日志文件占位
  ✅ P2-4：暴露 is_initialized() 供测试/健康检查使用
"""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
import os
import queue
import sys
import threading
import uuid
from contextvars import ContextVar
from pathlib import Path

# ================================================================
#  0. 从 cog 导入配置
# ================================================================
from task_planner.infrastructure.cog import hub as _hub

DEBUG = _hub.dev.DEBUG
LOG_CONSOLE_LEVEL = _hub.dev.LOG_CONSOLE_LEVEL
LOG_DIR = _hub.dev.LOG_DIR
LOG_FILE = _hub.dev.LOG_FILE
LOG_MAX_DAYS = _hub.dev.LOG_MAX_DAYS


# ✅ P1-9：模块级缓存项目根路径，避免重复计算
# logger_setup.py → infrastructure/ → task_planner/ → src/ → 项目根
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]


# ================================================================
#  1. 上下文变量：自动传递 req_id
# ================================================================
_req_id_var: ContextVar[str] = ContextVar("req_id", default="-")


def set_req_id(rid: str | None = None) -> str:
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
    """获取当前上下文的 req_id（永不返回 None，无值时返回 "-"）"""
    # ✅ P2-1：ContextVar.get() 已保证返回 default，但显式兜底更稳
    return _req_id_var.get() or "-"


def _make_req_id() -> str:
    """生成 8 字符短请求 ID"""
    return uuid.uuid7().hex[:-8]


def propagate_req_id() -> contextvars.Context:
    """
    复制当前上下文的 Context 对象。
    在创建子 Task 时用它包一层，让子 Task 也读得到 req_id。

    用法 1（asyncio.create_task）：
        ctx = propagate_req_id()
        task = asyncio.create_task(ctx.run(my_coro()))

    用法 2（asyncio.gather）：
        ctx = propagate_req_id()
        await asyncio.gather(
            ctx.run(node_a()),
            ctx.run(node_b()),
        )

    用法 3（to_thread）：
        ctx = propagate_req_id()
        result = await asyncio.to_thread(ctx.run, blocking_func, arg1, arg2)
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
            record.req_id = get_req_id()
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
        func: str | None = None,
        extra: dict | None = None,
        sinfo: str | None = None,
    ) -> logging.LogRecord:
        extra = dict(extra) if extra else {}
        extra.setdefault("req_id", get_req_id())
        return super().makeRecord(
            name, level, fn, lno, msg, args, exc_info,
            func=func, extra=extra, sinfo=sinfo,
        )


# ================================================================
#  3. 日志系统初始化（幂等，线程安全）
# ================================================================
_init_lock = threading.Lock()
_initialized = False
_queue_listener: logging.handlers.QueueListener | None = None
_log_queue: queue.Queue | None = None

# 根 logger 名（所有子模块都是它的子 logger）
_ROOT_LOGGER_NAME = "task_planner"


class _SafeQueueHandler(logging.handlers.QueueHandler):
    """
    队列满时记录 stderr 警告，不再静默丢弃。
    ✅ P1-7：getMessage() 加 try，防格式化异常连锁炸 stderr 写入。
    """

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            # 直接写 stderr，避免递归调用 logger
            try:
                msg = record.getMessage()[:80]
            except Exception:
                msg = "<格式化失败>"
            try:
                sys.stderr.write(
                    f"[logger_setup] 日志队列已满，丢弃日志: "
                    f"{record.name} {msg}\n"
                )
            except Exception:
                # stderr 都写不了就彻底放弃（绝不能拖垮主流程）
                pass


def setup_logger(name: str = _ROOT_LOGGER_NAME) -> ReqIdLogger:
    """
    获取或创建 logger（幂等）。

    ✅ P0-1：
      只在根 logger（"task_planner"）上挂 handler 和 QueueListener。
      其他 name（如 "task_planner.db"）作为子 logger，propagate=True 向上冒泡。
    """
    global _initialized

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


def _resolve_log_dir() -> str:
    """
    ✅ P1-6：LOG_DIR 空值兜底。
    优先级：非空 LOG_DIR > <项目根>/logs
    """
    log_dir = str(LOG_DIR).strip() if LOG_DIR else ""
    if not log_dir:
        log_dir = str(_PROJECT_ROOT / "logs")
    return log_dir


def _resolve_log_file() -> str:
    """
    ✅ P1-6：LOG_FILE 空值兜底。
    """
    log_file = str(LOG_FILE).strip() if LOG_FILE else ""
    if not log_file:
        log_file = "task_planner.log"
    return log_file


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

    # ── 控制台 Handler ──
    console_level = logging.DEBUG if DEBUG else logging.WARNING
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ReqIdFilter())

    # ── 文件 Handler（含 LOG_DIR / LOG_FILE 兜底） ──
    log_dir = _resolve_log_dir()
    log_file = _resolve_log_file()

    # ✅ P1-6：兜底后必须确保目录存在
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError as e:
        # 目录都建不了（权限/磁盘满）→ 降级为仅控制台
        sys.stderr.write(
            f"[logger_setup] 无法创建日志目录 {log_dir}: {e}，"
            f"已降级为仅控制台输出\n"
        )
        log_dir = None  # type: ignore

    file_handler: logging.Handler | None = None
    if log_dir is not None:
        log_path = os.path.join(log_dir, log_file)
        try:
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=log_path,
                when="midnight",
                interval=1,
                backupCount=LOG_MAX_DAYS,
                encoding="utf-8",
                delay=True,   # ✅ P2-3：首次写入才创建文件
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(ReqIdFilter())
        except OSError as e:
            sys.stderr.write(
                f"[logger_setup] 无法打开日志文件 {log_path}: {e}，"
                f"已降级为仅控制台输出\n"
            )
            file_handler = None

    # ── 队列 + QueueHandler（主线程侧） ──
    _log_queue = queue.Queue(maxsize=10000)
    queue_handler = _SafeQueueHandler(_log_queue)
    queue_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(queue_handler)

    # ── QueueListener（后台线程侧） ──
    handlers: list[logging.Handler] = [console_handler]
    if file_handler is not None:
        handlers.append(file_handler)

    _queue_listener = logging.handlers.QueueListener(
        _log_queue,
        *handlers,
        respect_handler_level=True,
    )
    _queue_listener.start()
    root_logger._queue_listener = _queue_listener  # type: ignore

    # ✅ P1-2：让第三方库的 WARNING+ 也能看到
    logging.getLogger().setLevel(logging.WARNING)
    if not logging.getLogger().handlers:
        root_console = logging.StreamHandler()
        root_console.setLevel(logging.WARNING)
        root_console.setFormatter(formatter)
        logging.getLogger().addHandler(root_console)

    # 打印启动信息
    root_logger.debug(
        "QueueListener 已启动 (队列容量=%d, 控制台级别=%s, 文件级别=DEBUG, "
        "根logger=%s, 日志目录=%s)",
        _log_queue.maxsize,
        logging.getLevelName(console_level),
        _ROOT_LOGGER_NAME,
        log_dir or "(无，仅控制台)",
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


def is_initialized() -> bool:
    """
    ✅ P2-4：日志系统是否已初始化。
    供测试 / 健康检查使用。
    """
    return _initialized


def shutdown_logging(timeout: float = 2.0) -> None:
    """
    优雅关闭日志系统：等待队列清空后停止 listener。

    ✅ P1-3：用 print 记录（listener 已停，logger 写入会失败）。
    ✅ P1-8：清理 root_logger 上的 handler，避免重新初始化时重复挂。
    """
    global _queue_listener, _initialized

    # ── 1. 停止 QueueListener ──
    if _queue_listener is not None:
        try:
            _queue_listener.stop()
        except Exception:
            pass
        _queue_listener = None

    # ── 2. 清理 root_logger 上的 handlers ──
    # ✅ P1-8：防止重新初始化时挂上第二个 QueueHandler 导致日志翻倍
    root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    _initialized = False

    # listener 已停，用 print 记录（不能再用 logger）
    try:
        print(
            "[logger_setup] 📦 QueueListener 已关闭，所有日志已写入",
            file=sys.stderr,
        )
    except Exception:
        pass


__all__ = [
    "ReqIdLogger",
    "ReqIdFilter",
    "get_logger",
    "setup_logger",
    "set_req_id",
    "get_req_id",
    "propagate_req_id",
    "shutdown_logging",
    "is_initialized",
]
