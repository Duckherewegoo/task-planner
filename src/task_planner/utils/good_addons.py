# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.py v6.2 — Enterprise Runtime Enhancement Layer        ║
# ║  Pyright strict | PEP-8 compliant | Python 3.9+                    ║
# ║  License: GPLv3                                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
good_addons v6.2 — 企业级 Python 运行时增强层。
═══════════════════════════════════════════════════════════════════════
单文件 drop-in 增强库：复制到任何项目即可 boost()。

Changelog:

  ── v6.0（vs v5.0）──
  ✅ 新增 @timed 轻量级计时装饰器
  ✅ 新增 @fallback 优雅降级装饰器
  ✅ 新增 HealthChecker 健康检查组件
  ✅ 分布式追踪：自动从 HTTP Header 提取 trace_id
  ✅ 结构化日志：可选的 JSON 格式输出
  ✅ 日志级别动态调整：set_log_level()
  ✅ diag() 增强：集成健康检查输出
  ✅ EventBus.get_history() 替代直接访问 _history

  ── v6.1 ──
  ✅ HealthChecker 重写：
       - 修复"在已有事件循环中调用 run() 死锁"
         现在降级为 degraded + 提示用 run_async()
       - 新增 run_async() / format_async() 异步版本
       - register() 支持"直接调用"和"装饰器"两种用法
  ✅ _setup_logging 支持重建：检测 JSON 格式变化，自动清空 handler
  ✅ boost(json_log=True) 现在能真正生效（不再被 import 时的旧 handler 吃掉）

  ── v6.2 ──
  ✅ P1-1：系统健康检查的内存阈值统一。
           原硬编码 `1024 * 0.9` 与 boost(memory_limit_mb=2048) 不一致，
           导致用户设 4G 时 900MB 就告警。现按 boost 参数动态计算。
  ✅ P1-2：_eventbus_health 改用公开的 EventBus.subscriber_count()，
           不再通过 getattr(EVENTS, "_subs") 访问私有属性。
  ✅ P1-3：@trace 装饰器的 finally 块简化。
           原 async/sync 两处 if/else 重复计算 ms，现统一为一行。
  ✅ P2-4：EventBus.emit / emit_async 的异常 stderr 输出加注释说明
           为何有意使用 print 而非 logger（防 log 未初始化 / 递归）。

Usage:
    from good_addons import boost

    # 纯后端增强
    boost()

    # Web 框架增强（自动识别 Dash / Flask）
    boost(app)

    # 常用组合
    boost(
        app,
        log_level="INFO",
        memory_watchdog=True,
        memory_limit_mb=2048,
        graceful_shutdown=True,
        json_log=False,        # True 时输出 JSON 格式日志
        csp_policy=None,       # None 时自动选择 Dash 兼容 / 严格策略
    )

设计说明：
  - 单文件可 drop-in，避免拆包破坏"复制一个 .py 就能用"的核心属性
  - 所有可选依赖（psutil / rich / orjson / pydantic / prometheus / cryptography）
    都走 try-import + 优雅降级，缺哪个都不影响 boost() 启动
  - 全局单例走 _SingletonMeta，线程安全

跨模块依赖（仅本项目内）：
  - 无。本文件保持零内部依赖，便于复制到其他项目。
"""

from __future__ import annotations

import asyncio
import contextvars
import enum
import functools
import hashlib
import html
import inspect
import json
import logging
import os
import re
import secrets
import signal
import statistics
import sys
import threading
import time
import traceback
from collections import (
    defaultdict,
    deque,
)
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
    TypeVar,
    Union,
    cast,
    overload,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Optional Dependencies (Graceful Degradation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_HAS_PSUTIL = False
_HAS_RICH = False
_HAS_ORJSON = False
_HAS_PYDANTIC = False
_HAS_PROMETHEUS = False
_HAS_CRYPTO = False

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore

try:
    from rich import box
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.table import Table
    _HAS_RICH = True
except ImportError:
    Console = None  # type: ignore
    RichHandler = None  # type: ignore
    Table = None  # type: ignore
    box = None  # type: ignore

try:
    import orjson
    _HAS_ORJSON = True
except ImportError:
    orjson = None  # type: ignore

try:
    import pydantic
    _HAS_PYDANTIC = True
except ImportError:
    pydantic = None  # type: ignore

try:
    import prometheus_client
    _HAS_PROMETHEUS = True
except ImportError:
    prometheus_client = None  # type: ignore

try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except ImportError:
    Fernet = None  # type: ignore

__version__ = "6.2.0"
__all__ = [
    "boost", "trace", "timed", "retry", "circuit_breaker", "rate_limited",
    "cached", "validated", "guarded", "fallback", "debounce", "memoize",
    "EventBus", "RequestContext", "CircuitBreaker", "CircuitOpenError",
    "RateLimitError", "PerformanceMonitor", "SystemMonitor", "Security",
    "MetricsCollector", "AuditLog", "GracefulShutdown", "HealthChecker",
    "ExceptionFingerprinter", "log", "get_logger", "set_log_level",
    "fast_json_dumps", "fast_json_loads", "diag",
]

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Global Context Variables
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_ctx_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_ctx_request_start: contextvars.ContextVar[float] = contextvars.ContextVar("request_start", default=0.0)
_ctx_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")


# ╔══════════════════════════════════════════════════╗
# ║  Part 1: Core Infrastructure                    ║
# ╚══════════════════════════════════════════════════╝

class _SingletonMeta(type):
    """Thread-safe Singleton Metaclass."""
    _instances: dict[type, Any] = {}
    _lock = threading.Lock()

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class EventBus(metaclass=_SingletonMeta):
    """Thread-safe Pub/Sub Event Bus."""

    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[int, Callable[..., Any]]]] = defaultdict(list)
        self._lock = threading.Lock()
        self._history: deque[dict[str, Any]] = deque(maxlen=1000)
        self._seq = 0

    def on(self, event: str, handler: Callable[..., Any], *, priority: int = 0) -> Callable[[], None]:
        with self._lock:
            self._subs[event].append((priority, handler))
            self._subs[event].sort(key=lambda x: -x[0])
        return lambda: self.off(event, handler)

    def off(self, event: str, handler: Callable[..., Any]) -> None:
        with self._lock:
            self._subs[event] = [(p, h) for p, h in self._subs[event] if h is not handler]

    def emit(self, event: str, **kwargs: Any) -> list[Any]:
        with self._lock:
            handlers = list(self._subs.get(event, []))
        self._seq += 1
        self._history.append({"event": event, "kwargs": kwargs, "time": time.time(), "seq": self._seq})
        results = []
        for _, handler in handlers:
            try:
                results.append(handler(**kwargs))
            except Exception as e:
                # 有意用 print 而非 log：EventBus 可能在 log 初始化前就被调用，
                # 且 handler 异常若走 logger 可能递归（log 自身也订阅事件的话）
                print(f"[EventBus] Error in {handler.__name__} for {event}: {e}", file=sys.stderr)
        return results

    async def emit_async(self, event: str, **kwargs: Any) -> list[Any]:
        with self._lock:
            handlers = list(self._subs.get(event, []))
        self._seq += 1
        self._history.append({"event": event, "kwargs": kwargs, "time": time.time(), "seq": self._seq})
        results = []
        for _, handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    results.append(await handler(**kwargs))
                else:
                    results.append(handler(**kwargs))
            except Exception as e:
                # 有意用 print 而非 log：EventBus 可能在 log 初始化前就被调用，
                # 且 handler 异常若走 logger 可能递归（log 自身也订阅事件的话）
                print(f"[EventBus] Async error in {handler.__name__} for {event}: {e}", file=sys.stderr)
        return results

    def get_history(self, last_n: int = 50) -> list[dict[str, Any]]:
        """返回最近 N 条事件历史（v6.0 新增，替代直接访问 _history）。"""
        with self._lock:
            return list(self._history)[-last_n:]

    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._subs.values())


class RequestContext:
    """Request-scoped context management (async/thread safe)."""

    # ── v6.0: 支持从 HTTP Header 提取 trace_id ──
    _trace_id_headers = ("X-Trace-ID", "X-Request-ID", "X-Correlation-ID")

    @staticmethod
    @contextmanager
    def scope(trace_id: str = "", user_id: str = "", **extra: Any) -> Generator[dict[str, Any]]:
        tid = trace_id or secrets.token_hex(8)
        t1 = _ctx_trace_id.set(tid)
        t2 = _ctx_request_start.set(time.perf_counter())
        t3 = _ctx_user_id.set(user_id)
        ctx = {"trace_id": tid, "user_id": user_id, **extra}
        try:
            yield ctx
        finally:
            _ctx_trace_id.reset(t1)
            _ctx_request_start.reset(t2)
            _ctx_user_id.reset(t3)

    @staticmethod
    def from_headers(headers: dict[str, str]) -> str:
        """v6.0: 从 HTTP Headers 提取 trace_id（支持 X-Trace-ID / X-Request-ID）。"""
        for header in RequestContext._trace_id_headers:
            val = headers.get(header, "")
            if val:
                _ctx_trace_id.set(val)
                return val
        return ""

    @staticmethod
    def trace_id() -> str:
        return _ctx_trace_id.get()

    @staticmethod
    def user_id() -> str:
        return _ctx_user_id.get()

    @staticmethod
    def elapsed() -> float:
        start = _ctx_request_start.get()
        return time.perf_counter() - start if start else 0.0

    @staticmethod
    def snapshot() -> dict[str, Any]:
        return {
            "trace_id": RequestContext.trace_id(),
            "user_id": RequestContext.user_id(),
            "elapsed_ms": round(RequestContext.elapsed() * 1000, 2),
        }


class PerformanceMonitor(metaclass=_SingletonMeta):
    """Global performance metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, dict[str, Any]] = {}

    def record(self, name: str, duration_ms: float, *, error: bool = False) -> None:
        with self._lock:
            if name not in self._stats:
                self._stats[name] = {"count": 0, "errors": 0, "total_ms": 0.0, "times": deque(maxlen=5000)}
            s = self._stats[name]
            s["count"] += 1
            s["total_ms"] += duration_ms
            s["times"].append(duration_ms)
            if error:
                s["errors"] += 1

    def snapshot(self, *, top_n: int = 0, sort_by: str = "total_ms") -> list[dict[str, Any]]:
        with self._lock:
            items = []
            for name, s in self._stats.items():
                times = list(s["times"])
                if len(times) < 2:
                    continue
                try:
                    q = statistics.quantiles(times, n=100)
                    p50, p95, p99 = q[49], q[94], q[98]
                except Exception:
                    p50 = p95 = p99 = times[0]
                items.append({
                    "name": name, "calls": s["count"], "errors": s["errors"],
                    "total_ms": round(s["total_ms"], 2),
                    "avg_ms": round(s["total_ms"] / s["count"], 2),
                    "p50_ms": round(p50, 2), "p95_ms": round(p95, 2), "p99_ms": round(p99, 2),
                    "max_ms": round(max(times), 2),
                })
            items.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
            return items[:top_n] if top_n else items

    def format_table(self, *, top_n: int = 20) -> str:
        items = self.snapshot(top_n=top_n)
        if not items:
            return "(no data)"
        header = f"{'Function':<40} {'Calls':>7} {'Avg':>8} {'P50':>8} {'P95':>8} {'P99':>8} {'Err':>5}"
        sep = "─" * len(header)
        rows = [sep, header, sep]
        for i in items:
            rows.append(
                f"{i['name'][:40]:<40} {i['calls']:>7} {i['avg_ms']:>7.1f}ms "
                f"{i['p50_ms']:>7.1f}ms {i['p95_ms']:>7.1f}ms {i['p99_ms']:>7.1f}ms {i['errors']:>5}"
            )
        rows.append(sep)
        return "\n".join(rows)


# ╔══════════════════════════════════════════════════╗
# ║  Part 2: Logging & Sanitization                 ║
# ╚══════════════════════════════════════════════════╝

_SENSITIVE_PATTERNS = [
    (re.compile(r"(password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*\S+", re.I), r"\1=***"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "***ID_CARD***"),
    (re.compile(r"\b(?:Bearer\s+)?[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{20,}\b"), "***JWT***"),
]

# ── v6.0: 结构化日志格式开关 ──
_LOG_JSON_FORMAT = os.getenv("GOOD_ADDONS_LOG_JSON", "false").lower() == "true"


class _ContextFilter(logging.Filter):
    """Injects context variables into log records."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _ctx_trace_id.get() or "-"  # type: ignore
        record.user_id = _ctx_user_id.get() or "-"    # type: ignore
        return True


class _SanitizeFilter(logging.Filter):
    """Redacts sensitive information from log messages."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for pattern, repl in _SENSITIVE_PATTERNS:
                msg = pattern.sub(repl, msg)
            record.msg = msg
            record.args = ()
        except Exception:
            pass
        return True


class _JSONFormatter(logging.Formatter):
    """v6.0: JSON 格式日志格式化器。"""
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "file": f"{record.filename}:{record.lineno}",
        }
        if record.exc_info:
            data["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return fast_json_dumps(data)


_ctx_filter = _ContextFilter()
_sanitize_filter = _SanitizeFilter()
_json_formatter = _JSONFormatter()


def _setup_logging(
    level: str = "INFO",
    *,
    rich: bool = True,
    json_format: bool | None = None,
) -> logging.Logger:
    """
    初始化 good_addons logger。

    ✅ P0-2 修复：
      - 支持重复调用：若日志格式（JSON vs 文本）变化，自动清空 handler 重建。
      - 这样 boost(json_log=True) 能真正生效，而不是被 import 时的旧 handler 吃掉。

    Args:
        level: 日志级别（"INFO" / "DEBUG" / ...）
        rich: 是否尝试使用 RichHandler
        json_format: 是否 JSON 格式；None 时读全局 _LOG_JSON_FORMAT
    """
    logger = logging.getLogger("good_addons")

    target_json = json_format if json_format is not None else _LOG_JSON_FORMAT
    current_json = getattr(logger, "_log_json_format", None)

    # ── 已初始化且配置未变 → 直接返回 ──
    if logger.handlers and current_json == target_json:
        # 仅调整级别
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        return logger

    # ── 配置变了（或首次初始化）→ 清空重建 ──
    if logger.handlers:
        for h in list(logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            logger.removeHandler(h)
        for f in list(logger.filters):
            logger.removeFilter(f)

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    logger.addFilter(_ctx_filter)
    logger.addFilter(_sanitize_filter)

    # ── 根据 target_json 选择 handler ──
    if target_json:
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_json_formatter)
    elif rich and _HAS_RICH and Console and RichHandler:
        console = Console(stderr=True)
        handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        handler.setFormatter(logging.Formatter("[dim]%(trace_id)s[/] %(message)s"))
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s │ %(levelname)-7s │ %(trace_id)s │ %(message)s",
            datefmt="%H:%M:%S",
        ))

    handler.addFilter(_ctx_filter)
    handler.addFilter(_sanitize_filter)
    logger.addHandler(handler)

    # ✅ 记录本次格式，供下次判断
    logger._log_json_format = target_json  # type: ignore

    # ── root logger 兜底（仅在无 handler 时添加） ──
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stderr))
    root.addFilter(_ctx_filter)
    root.addFilter(_sanitize_filter)

    return logger

log: logging.Logger = _setup_logging()


def get_logger(name: str = "") -> logging.Logger:
    """Get a logger with context and sanitization filters."""
    logger = logging.getLogger(name or "good_addons")
    logger.addFilter(_ctx_filter)
    logger.addFilter(_sanitize_filter)
    return logger


def set_log_level(level: Union[str, int]) -> None:
    """
    v6.0: 运行时安全修改日志级别。
    同时修改 root logger 和 good_addons logger。
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(level)
    logging.getLogger("good_addons").setLevel(level)
    log.info("📊 Log level changed to %s", logging.getLevelName(level))


# ╔══════════════════════════════════════════════════╗
# ║  Part 3: Exception Fingerprinting               ║
# ╚══════════════════════════════════════════════════╝

class ExceptionFingerprinter:
    """Generates stable fingerprints for exceptions + deduplication."""

    _seen: dict[str, float] = {}
    _lock = threading.Lock()
    _dedup_window: float = 60.0

    @staticmethod
    def fingerprint(exc: BaseException) -> str:
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        normalized = [line.strip() for line in tb_lines if not line.startswith("  File ")]
        raw = f"{type(exc).__name__}:{exc}|{'|'.join(normalized[-3:])}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    @classmethod
    def capture(cls, exc: BaseException) -> dict[str, Any]:
        fp = cls.fingerprint(exc)
        now = time.time()
        with cls._lock:
            last = cls._seen.get(fp, 0)
            if now - last < cls._dedup_window:
                return {"fingerprint": fp, "deduplicated": True}
            cls._seen[fp] = now
            if len(cls._seen) > 10000:
                cutoff = now - cls._dedup_window * 2
                cls._seen = {k: v for k, v in cls._seen.items() if v > cutoff}

        snapshot = RequestContext.snapshot()
        if 'AUDIT' in globals():
            globals()['AUDIT'].record("exception", details={
                "fingerprint": fp, "type": type(exc).__name__,
                "message": str(exc)[:500], "context": snapshot,
            })
        return {"fingerprint": fp, "deduplicated": False, "context": snapshot}


def _install_exception_hook() -> None:
    """Install global exception hook."""
    original_hook = sys.excepthook

    def _hook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        try:
            info = ExceptionFingerprinter.capture(exc_value)
            if not info.get("deduplicated"):
                log.error(
                    "💥 Unhandled exception [%s] | ctx=%s",
                    info["fingerprint"], info.get("context"),
                    exc_info=(exc_type, exc_value, exc_tb),
                )
        except Exception:
            pass
        original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


# ╔══════════════════════════════════════════════════╗
# ║  Part 4: Decorator Arsenal                      ║
# ╚══════════════════════════════════════════════════╝

def _safe_repr(args: Any, kwargs: Any, max_len: int = 200) -> str:
    try:
        parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
        res = ", ".join(parts)
        return res[:max_len] + "..." if len(res) > max_len else res
    except Exception:
        return "<repr failed>"


@overload
def trace(func: F) -> F: ...
@overload
def trace(*, level: int = logging.DEBUG, slow_ms: float = 200, log_args: bool = False, memory: bool = False, name: str = "") -> Callable[[F], F]: ...

def trace(
    _func: F | None = None, *, level: int = logging.DEBUG, slow_ms: float = 200,
    log_args: bool = False, memory: bool = False, name: str = "",
) -> Union[F, Callable[[F], F]]:
    """Performance tracing decorator."""
    def decorator(func: F) -> F:
        fname = name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            mem_before = psutil.Process().memory_info().rss if memory and _HAS_PSUTIL and psutil else 0
            tid = secrets.token_hex(4)
            old = _ctx_trace_id.set(_ctx_trace_id.get() or tid)
            _recorded = False
            try:
                if log_args:
                    log.log(level, "→ %s(%s)", fname, _safe_repr(args, kwargs))
                return await func(*args, **kwargs)
            except Exception:
                ms = (time.perf_counter() - t0) * 1000
                PERF.record(fname, ms, error=True)
                log.error("✗ %s FAILED %.1fms", fname, ms)
                _recorded = True
                raise
            finally:
                _ctx_trace_id.reset(old)
                # ✅ P1-3：统一计算 ms，避免 if/else 分支重复
                ms = (time.perf_counter() - t0) * 1000
                if not _recorded:
                    PERF.record(fname, ms)
                if ms > slow_ms:
                    log.warning("⏱ SLOW %s: %.1fms (threshold: %.0fms)", fname, ms, slow_ms)
                if memory and _HAS_PSUTIL and psutil:
                    delta = psutil.Process().memory_info().rss - mem_before
                    if abs(delta) > 1024 * 1024:
                        log.info("🧠 %s mem Δ: %+.1fMB", fname, delta / (1024 * 1024))
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            mem_before = psutil.Process().memory_info().rss if memory and _HAS_PSUTIL and psutil else 0
            tid = secrets.token_hex(4)
            old = _ctx_trace_id.set(_ctx_trace_id.get() or tid)
            _recorded = False
            try:
                if log_args:
                    log.log(level, "→ %s(%s)", fname, _safe_repr(args, kwargs))
                return func(*args, **kwargs)
            except Exception:
                ms = (time.perf_counter() - t0) * 1000
                PERF.record(fname, ms, error=True)
                log.error("✗ %s FAILED %.1fms", fname, ms)
                _recorded = True
                raise
            finally:
                _ctx_trace_id.reset(old)
                # ✅ P1-3：统一计算 ms，避免 if/else 分支重复
                ms = (time.perf_counter() - t0) * 1000
                if not _recorded:
                    PERF.record(fname, ms)
                if ms > slow_ms:
                    log.warning("⏱ SLOW %s: %.1fms (threshold: %.0fms)", fname, ms, slow_ms)
                if memory and _HAS_PSUTIL and psutil:
                    delta = psutil.Process().memory_info().rss - mem_before
                    if abs(delta) > 1024 * 1024:
                        log.info("🧠 %s mem Δ: %+.1fMB", fname, delta / (1024 * 1024))
        if inspect.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    if _func is not None:
        return decorator(_func)
    return decorator


# ── v6.0: @timed 轻量级计时装饰器 ──

@overload
def timed(func: F) -> F: ...
@overload
def timed(*, slow_ms: float = 200, name: str = "") -> Callable[[F], F]: ...

def timed(
    _func: F | None = None, *, slow_ms: float = 200, name: str = "",
) -> Union[F, Callable[[F], F]]:
    """
    v6.0: 轻量级性能计时装饰器（@trace 的简化版）。
    仅记录耗时，不记录参数、不追踪内存。
    """
    def decorator(func: F) -> F:
        fname = name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                ms = (time.perf_counter() - t0) * 1000
                PERF.record(fname, ms)
                if ms > slow_ms:
                    log.warning("⏱ SLOW %s: %.1fms", fname, ms)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                ms = (time.perf_counter() - t0) * 1000
                PERF.record(fname, ms)
                if ms > slow_ms:
                    log.warning("⏱ SLOW %s: %.1fms", fname, ms)

        if inspect.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    if _func is not None:
        return decorator(_func)
    return decorator


# ── v6.0: @fallback 优雅降级装饰器 ──

@overload
def fallback(func: F) -> F: ...
@overload
def fallback(*, value: Any = None, catch: tuple[type[Exception], ...] = (Exception,), log_error: bool = True) -> Callable[[F], F]: ...

def fallback(
    _func: F | None = None, *, value: Any = None,
    catch: tuple[type[Exception], ...] = (Exception,), log_error: bool = True,
) -> Union[F, Callable[[F], F]]:
    """
    v6.0: 优雅降级装饰器——主函数失败时返回备用值，不抛出异常。
    适用于非关键路径的降级场景。

    Example:
        @fallback(value=[])
        def fetch_data() -> List[str]:
            return api_call()  # 失败时返回空列表
    """
    def decorator(func: F) -> F:
        fallback_value = value() if callable(value) else value

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except catch as e:
                if log_error:
                    log.warning("🛡 %s fallback triggered: %s", func.__qualname__, e)
                return fallback_value

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except catch as e:
                if log_error:
                    log.warning("🛡 %s fallback triggered: %s", func.__qualname__, e)
                return fallback_value

        if inspect.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    if _func is not None:
        return decorator(_func)
    return decorator


@overload
def retry(func: F) -> F: ...
@overload
def retry(*, max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 60.0, exponential: bool = True, jitter: bool = True, exceptions: tuple[type[Exception], ...] = (Exception,), backoff_factor: float = 2.0) -> Callable[[F], F]: ...

def retry(
    _func: F | None = None, *, max_attempts: int = 3, base_delay: float = 1.0,
    max_delay: float = 60.0, exponential: bool = True, jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,), backoff_factor: float = 2.0,
) -> Union[F, Callable[[F], F]]:
    """Retry decorator with exponential backoff."""
    def decorator(func: F) -> F:
        def _delay(attempt: int) -> float:
            d = base_delay * (backoff_factor ** (attempt - 1)) if exponential else base_delay
            d = min(d, max_delay)
            if jitter:
                d *= (0.5 + secrets.randbelow(100) / 100.0)
            return d

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt >= max_attempts:
                        log.error("✗ %s failed after %d attempts: %s", func.__qualname__, max_attempts, e)
                        raise
                    delay = _delay(attempt)
                    log.warning("↻ %s attempt %d/%d failed (%s), retry in %.2fs", func.__qualname__, attempt, max_attempts, e, delay)
                    await asyncio.sleep(delay)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt >= max_attempts:
                        log.error("✗ %s failed after %d attempts: %s", func.__qualname__, max_attempts, e)
                        raise
                    delay = _delay(attempt)
                    log.warning("↻ %s attempt %d/%d failed (%s), retry in %.2fs", func.__qualname__, attempt, max_attempts, e, delay)
                    time.sleep(delay)

        if inspect.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    if _func is not None:
        return decorator(_func)
    return decorator


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    """Circuit Breaker pattern implementation."""

    def __init__(
        self, failure_threshold: int = 5, recovery_timeout: float = 30.0,
        success_threshold: int = 2, fallback: Callable[..., Any] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.fallback = fallback
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._last_fail_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN and (time.time() - self._last_fail_time >= self.recovery_timeout):
                self._state = CircuitState.HALF_OPEN
                self._successes = 0
                log.info("⚡ CircuitBreaker: OPEN → HALF_OPEN")
            return self._state

    # ── v6.0: 导出状态用于监控 ──
    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "failures": self._failures,
                "successes": self._successes,
                "last_fail_time": self._last_fail_time,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
            }

    def __call__(self, func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if self.state == CircuitState.OPEN:
                if self.fallback:
                    return self.fallback(*args, **kwargs)
                raise CircuitOpenError(f"Circuit OPEN for {func.__qualname__}")
            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
            except Exception:
                self._on_failure()
                raise

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if self.state == CircuitState.OPEN:
                if self.fallback:
                    return self.fallback(*args, **kwargs)
                raise CircuitOpenError(f"Circuit OPEN for {func.__qualname__}")
            try:
                result = await func(*args, **kwargs)
                self._on_success()
                return result
            except Exception:
                self._on_failure()
                raise

        if inspect.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    def _on_success(self) -> None:
        with self._lock:
            self._failures = 0
            if self._state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    log.info("⚡ CircuitBreaker: HALF_OPEN → CLOSED")

    def _on_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_fail_time = time.time()
            if self._failures >= self.failure_threshold or self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                log.warning("⚡ CircuitBreaker: → OPEN (failures: %d)", self._failures)


@overload
def circuit_breaker(func: F) -> F: ...
@overload
def circuit_breaker(*, failure_threshold: int = 5, recovery_timeout: float = 30.0, fallback: Callable[..., Any] | None = None) -> Callable[[F], F]: ...

def circuit_breaker(
    _func: F | None = None, *, failure_threshold: int = 5,
    recovery_timeout: float = 30.0, fallback: Callable[..., Any] | None = None,
) -> Union[F, Callable[[F], F]]:
    breaker = CircuitBreaker(failure_threshold, recovery_timeout, fallback=fallback)
    if _func is not None:
        return breaker(_func)
    return breaker


class _TokenBucket:
    def __init__(self, rate: float, capacity: int) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


class RateLimitError(Exception):
    pass


def rate_limited(rate: float = 10.0, capacity: int = 20, *, on_limit: Callable[..., Any] | None = None) -> Callable[[F], F]:
    bucket = _TokenBucket(rate, capacity)

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not bucket.acquire():
                if on_limit:
                    return on_limit(*args, **kwargs)
                raise RateLimitError(f"Rate limit exceeded for {func.__qualname__}")
            return func(*args, **kwargs)
        return cast(F, wrapper)
    return decorator


def cached(ttl: float = 300, *, maxsize: int = 1024, key_func: Callable[..., str] | None = None, jitter: float = 0.0) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        store: dict[str, tuple[Any, float]] = {}
        lock = threading.Lock()
        stats = {"hits": 0, "misses": 0, "evictions": 0}

        def _key(args: Any, kwargs: Any) -> str:
            if key_func:
                return key_func(*args, **kwargs)
            return hashlib.md5(str((func.__qualname__, repr(args), repr(sorted(kwargs.items())))).encode()).hexdigest()

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _key(args, kwargs)
            now = time.time()
            with lock:
                if key in store and now < store[key][1]:
                    stats["hits"] += 1
                    return store[key][0]
                if len(store) >= maxsize:
                    oldest = min(store, key=lambda k: store[k][1])
                    del store[oldest]
                    stats["evictions"] += 1
            result = func(*args, **kwargs)
            actual_ttl = ttl + (secrets.randbelow(int(jitter * 1000)) / 1000.0 if jitter > 0 else 0)
            with lock:
                store[key] = (result, now + actual_ttl)
                stats["misses"] += 1
            return result

        wrapper.cache_clear = lambda: store.clear()  # type: ignore
        wrapper.cache_stats = lambda: dict(stats)    # type: ignore
        return cast(F, wrapper)
    return decorator


def memoize(func: F) -> F:
    cache: dict[str, Any] = {}

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        key = hashlib.md5(str((func.__qualname__, repr(args), repr(sorted(kwargs.items())))).encode()).hexdigest()
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]
    wrapper.cache_clear = cache.clear  # type: ignore
    return cast(F, wrapper)


def validated(**type_hints: Any) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        if not _HAS_PYDANTIC or not type_hints:
            return func

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            for name, expected in type_hints.items():
                if name in bound.arguments and not isinstance(bound.arguments[name], expected):
                    try:
                        bound.arguments[name] = expected(bound.arguments[name])
                    except (ValueError, TypeError) as e:
                        raise TypeError(f"Param '{name}': expected {expected.__name__}, got {type(bound.arguments[name]).__name__} ({e})")
            return func(*bound.args, **bound.kwargs)
        return cast(F, wrapper)
    return decorator


@overload
def guarded(func: F) -> F: ...
@overload
def guarded(*, fallback: Any = None, catch: tuple[type[Exception], ...] = (Exception,), log_error: bool = True, reraise: bool = False) -> Callable[[F], F]: ...

def guarded(
    _func: F | None = None, *, fallback: Any = None,
    catch: tuple[type[Exception], ...] = (Exception,), log_error: bool = True, reraise: bool = False,
) -> Union[F, Callable[[F], F]]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except catch as e:
                if log_error:
                    log.error("🛡 %s caught %s: %s", func.__qualname__, type(e).__name__, e, exc_info=True)
                if reraise:
                    raise
                return fallback() if callable(fallback) else fallback

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except catch as e:
                if log_error:
                    log.error("🛡 %s caught %s: %s", func.__qualname__, type(e).__name__, e, exc_info=True)
                if reraise:
                    raise
                return fallback() if callable(fallback) else fallback

        if inspect.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    if _func is not None:
        return decorator(_func)
    return decorator


def debounce(wait: float = 0.3) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        timer: list[threading.Timer | None] = [None]
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            with lock:
                if timer[0]:
                    timer[0].cancel()
                timer[0] = threading.Timer(wait, func, args=args, kwargs=kwargs)
                timer[0].start()
        return cast(F, wrapper)
    return decorator


# ╔══════════════════════════════════════════════════╗
# ║  Part 5: Security & System                      ║
# ╚══════════════════════════════════════════════════╝

class Security:
    """Security utilities."""
    _XSS = [
        re.compile(r"<script[^>]*>.*?</script>", re.I | re.S),
        re.compile(r"javascript\s*:", re.I),
        re.compile(r"on\w+\s*=", re.I),
    ]
    _SQL = re.compile(r"\b(UNION|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC|TRUNCATE|--|;)\b", re.I)

    # --- v5.0: CSP Presets ---
    CSP_STRICT = "default-src 'self'"
    CSP_DASH_COMPAT = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss: https://dash-version.plotly.com;"
    )

    @staticmethod
    def sanitize_html(text: str) -> str:
        return html.escape(str(text), quote=True)

    @staticmethod
    def strip_xss(text: str) -> str:
        res = str(text)
        for p in Security._XSS:
            res = p.sub("", res)
        return res

    @staticmethod
    def detect_sql_injection(text: str) -> bool:
        return bool(Security._SQL.search(str(text)))

    @staticmethod
    def hash_password(password: str, *, salt: str = "") -> str:
        salt = salt or secrets.token_hex(16)
        return f"{salt}:{hashlib.sha256(f'{salt}{password}'.encode()).hexdigest()}"

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        try:
            salt, expected = hashed.split(":", 1)
            return secrets.compare_digest(hashlib.sha256(f"{salt}{password}".encode()).hexdigest(), expected)
        except ValueError:
            return False

    @staticmethod
    def encrypt(data: str, key: bytes | None = None) -> tuple[str, bytes]:
        if not _HAS_CRYPTO or not Fernet:
            raise ImportError("pip install cryptography")
        key = key or Fernet.generate_key()
        return Fernet(key).encrypt(data.encode()).decode(), key

    @staticmethod
    def decrypt(token: str, key: bytes) -> str:
        if not _HAS_CRYPTO or not Fernet:
            raise ImportError("pip install cryptography")
        return Fernet(key).decrypt(token.encode()).decode()

    @staticmethod
    def security_headers(csp: str | None = None) -> dict[str, str]:
        return {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": csp or Security.CSP_STRICT,
        }


class SystemMonitor:
    """System resource monitoring."""

    @staticmethod
    def memory_info() -> dict[str, Any]:
        if _HAS_PSUTIL and psutil:
            mem = psutil.Process().memory_info()
            return {"rss_mb": round(mem.rss / 1048576, 2), "vms_mb": round(mem.vms / 1048576, 2)}
        return {}

    @staticmethod
    def cpu_percent() -> float:
        if _HAS_PSUTIL and psutil:
            return psutil.Process().cpu_percent(interval=0.1)
        return 0.0

    @staticmethod
    def start_memory_watchdog(
        *, max_mb: float = 2048, interval: float = 30, callback: Callable[..., Any] | None = None,
    ) -> threading.Thread:
        def _watchdog() -> None:
            while True:
                time.sleep(interval)
                info = SystemMonitor.memory_info()
                if info.get("rss_mb", 0) > max_mb:
                    log.critical("🚨 Memory watchdog: %.0fMB > %.0fMB", info["rss_mb"], max_mb)
                    if callback:
                        callback(info)
                    EVENTS.emit("memory_critical", **info)

        t = threading.Thread(target=_watchdog, daemon=True, name="mem-watchdog")
        t.start()
        return t


class AuditLog(metaclass=_SingletonMeta):
    """Audit log collector."""

    def __init__(self, *, max_entries: int = 10000) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def record(self, action: str, *, actor: str = "", resource: str = "", details: Any = None) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "actor": actor or RequestContext.user_id() or "system",
            "trace_id": RequestContext.trace_id(),
            "resource": resource,
            "details": details,
        }
        with self._lock:
            self._entries.append(entry)
        return entry

    def query(self, *, action: str = "", actor: str = "", last_n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._entries)
        if action:
            entries = [e for e in entries if e["action"] == action]
        if actor:
            entries = [e for e in entries if e["actor"] == actor]
        return entries[-last_n:]


class MetricsCollector(metaclass=_SingletonMeta):
    """Metrics collector with optional Prometheus integration."""

    def __init__(self) -> None:
        self._counters: dict[str, Any] = {}
        self._simple: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def counter(self, name: str) -> None:
        with self._lock:
            self._simple[name] += 1
            if _HAS_PROMETHEUS and prometheus_client:
                if name not in self._counters:
                    self._counters[name] = prometheus_client.Counter(name, f"Counter: {name}")
                self._counters[name].inc()

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._simple)


class GracefulShutdown(metaclass=_SingletonMeta):
    """Graceful shutdown manager."""

    def __init__(self) -> None:
        self._handlers: list[tuple[int, Callable[..., Any]]] = []
        self._event = threading.Event()
        self._installed = False

    def register(self, handler: Callable[..., Any], *, priority: int = 0) -> None:
        self._handlers.append((priority, handler))
        self._handlers.sort(key=lambda x: -x[0])

    def install(self) -> None:
        if self._installed or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            return
        self._installed = True

        def _handler(signum: int, frame: Any) -> None:
            log.info("🛑 Received signal %s, shutting down...", signal.Signals(signum).name)
            self._event.set()
            for _, h in self._handlers:
                try:
                    h()
                except Exception as e:
                    log.error("Shutdown handler error: %s", e)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


# ╔══════════════════════════════════════════════════╗
# ║  Part 6: HealthChecker (v6.1 NEW)               ║
# ╚══════════════════════════════════════════════════╝

class HealthChecker(metaclass=_SingletonMeta):
    """
    v6.0: 统一健康检查组件。
    各模块可注册健康检查函数，输出结构化健康报告。

    修复说明：
      - P0-1：run() 在异步上下文中不再死锁（通过 asyncio.run 检测 + 新增 run_async）
      - P0-3：register() 同时支持"直接调用"和"装饰器"两种用法
    """

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[], Any]] = {}
        self._lock = threading.Lock()

    @overload
    def register(
        self, name: str,
    ) -> Callable[[Callable[[], dict[str, Any]]], Callable[[], dict[str, Any]]]: ...
    @overload
    def register(
        self, name: str, check_fn: Callable[[], dict[str, Any]],
    ) -> Callable[[], dict[str, Any]]: ...

    def register(
        self,
        name: str,
        check_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> Union[
        Callable[[Callable[[], dict[str, Any]]], Callable[[], dict[str, Any]]],
        Callable[[], dict[str, Any]],
    ]:
        """
        ✅ P0-3 修复：同时支持两种用法。

        用法 1（装饰器）：
            @HEALTH.register("db")
            def check_db() -> Dict[str, Any]:
                return {"status": "ok"}

        用法 2（直接调用）：
            HEALTH.register("db", check_db)
        """
        def _do_register(
            fn: Callable[[], dict[str, Any]],
        ) -> Callable[[], dict[str, Any]]:
            with self._lock:
                self._checks[name] = fn
            log.debug("🩺 HealthCheck registered: %s", name)
            # 关键：返回 fn 本身，不破坏装饰器链
            return fn

        if check_fn is None:
            return _do_register
        return _do_register(check_fn)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._checks.pop(name, None)

    def _snapshot_checks(self) -> dict[str, Callable[[], Any]]:
        with self._lock:
            return dict(self._checks)

    def _normalize_result(self, result: Any) -> dict[str, Any]:
        """把任意返回值规范成 {"status": ..., ...} 格式。"""
        if not isinstance(result, dict):
            return {"status": "degraded", "value": result}
        result.setdefault("status", "ok")
        return result

    def _aggregate_overall(self, results: dict[str, dict[str, Any]]) -> str:
        overall = "ok"
        for r in results.values():
            st = r.get("status")
            if st == "down":
                return "down"
            if st == "degraded" and overall != "down":
                overall = "degraded"
        return overall

    def run(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        同步运行所有健康检查。

        ✅ P0-1 修复：在已有运行中事件循环的上下文中，
          异步检查函数不再导致死锁，而是降级为 degraded，
          并提示调用方改用 `await run_async()`。
        """
        # 检测是否存在运行中的事件循环
        has_running_loop = False
        try:
            asyncio.get_running_loop()
            has_running_loop = True
        except RuntimeError:
            has_running_loop = False

        results: dict[str, dict[str, Any]] = {}
        for name, fn in self._snapshot_checks().items():
            if inspect.iscoroutinefunction(fn):
                if has_running_loop:
                    # ✅ 不能在这里跑协程，明确降级而非阻塞
                    results[name] = {
                        "status": "degraded",
                        "error": (
                            "async check skipped in sync context "
                            "(running loop detected); use `await HEALTH.run_async()`"
                        ),
                    }
                    continue
                # 无运行中循环 → 可以用 asyncio.run
                try:
                    result = asyncio.run(asyncio.wait_for(fn(), timeout=timeout))
                except asyncio.TimeoutError:
                    result = {"status": "down", "error": f"timeout after {timeout}s"}
                except Exception as e:
                    result = {"status": "down", "error": str(e)}
            else:
                # 同步函数：直接调用
                try:
                    result = fn()
                except Exception as e:
                    result = {"status": "down", "error": str(e)}

            results[name] = self._normalize_result(result)

        return {
            "status": self._aggregate_overall(results),
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": results,
        }

    async def run_async(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        ✅ P0-1 修复：异步版本，推荐在 asyncio 上下文里使用。

        - 异步检查函数 → `await asyncio.wait_for(fn(), timeout)`
        - 同步检查函数 → 直接调用（如很慢应改为 async）
        """
        results: dict[str, dict[str, Any]] = {}
        for name, fn in self._snapshot_checks().items():
            try:
                if inspect.iscoroutinefunction(fn):
                    result = await asyncio.wait_for(fn(), timeout=timeout)
                else:
                    result = fn()
            except asyncio.TimeoutError:
                result = {"status": "down", "error": f"timeout after {timeout}s"}
            except Exception as e:
                result = {"status": "down", "error": str(e)}

            results[name] = self._normalize_result(result)

        return {
            "status": self._aggregate_overall(results),
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": results,
        }

    def format(self, timeout: float = 5.0) -> str:
        """
        返回格式化的健康报告（同步版本）。

        提示：如在异步上下文中调用，异步检查项会显示为 degraded。
        """
        report = self.run(timeout)
        lines = [
            "═══ Health Report ═══",
            f"Status: {report['status'].upper()}",
            f"Time:  {report['timestamp']}",
            "",
        ]
        for name, result in report["checks"].items():
            status = result.get("status", "unknown")
            icon = "✅" if status == "ok" else "🟡" if status == "degraded" else "❌"
            lines.append(f"  {icon} {name}: {status}")
            if result.get("error"):
                lines.append(f"      error: {result['error']}")
            if result.get("details"):
                lines.append(f"      {result['details']}")
        return "\n".join(lines)

    async def format_async(self, timeout: float = 5.0) -> str:
        """异步版本 format，推荐在 asyncio 上下文里使用。"""
        report = await self.run_async(timeout)
        lines = [
            "═══ Health Report ═══",
            f"Status: {report['status'].upper()}",
            f"Time:  {report['timestamp']}",
            "",
        ]
        for name, result in report["checks"].items():
            status = result.get("status", "unknown")
            icon = "✅" if status == "ok" else "🟡" if status == "degraded" else "❌"
            lines.append(f"  {icon} {name}: {status}")
            if result.get("error"):
                lines.append(f"      error: {result['error']}")
            if result.get("details"):
                lines.append(f"      {result['details']}")
        return "\n".join(lines)

# ── v6.0: 自动注册系统健康检查 ──
_HEALTH = HealthChecker()

# 内存上限（由 boost(memory_limit_mb=...) 设置）
_memory_limit_mb: float = 2048.0


@_HEALTH.register("system")
def _system_health() -> dict[str, Any]:
    mem = SystemMonitor.memory_info()
    cpu = SystemMonitor.cpu_percent()
    rss = mem.get("rss_mb", 0)
    # ✅ P1-1：阈值与 boost(memory_limit_mb=...) 一致
    status = "degraded" if rss > _memory_limit_mb * 0.9 else "ok"
    return {
        "status": status,
        "details": f"RSS: {rss:.1f}MB / limit {_memory_limit_mb:.0f}MB, CPU: {cpu:.1f}%",
    }

@_HEALTH.register("eventbus")
def _eventbus_health() -> dict[str, Any]:
    """通过公开 API 读取 EventBus 状态。"""
    history = EVENTS.get_history(last_n=1000)
    subs_count = EVENTS.subscriber_count()
    return {
        "status": "ok",
        "details": f"subscribers: {subs_count}, history: {len(history)}",
    }

# ╔══════════════════════════════════════════════════╗
# ║  Part 7: JSON & Web Enhancement                 ║
# ╚══════════════════════════════════════════════════╝

def fast_json_dumps(obj: Any, *, pretty: bool = False) -> str:
    """
    v6.0 微调: default 处理更规范。
    支持 datetime、set 等特殊类型。
    """
    def _default_encoder(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, set):
            return list(o)
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    if _HAS_ORJSON and orjson:
        opts = orjson.OPT_NON_STR_KEYS | (orjson.OPT_INDENT_2 if pretty else 0)
        return orjson.dumps(obj, default=_default_encoder, option=opts).decode()
    return json.dumps(obj, default=_default_encoder, ensure_ascii=False, indent=2 if pretty else None)


def fast_json_loads(data: Union[str, bytes]) -> Any:
    if _HAS_ORJSON and orjson:
        return orjson.loads(data.encode() if isinstance(data, str) else data)
    return json.loads(data)


def _is_dash_app(app: Any) -> bool:
    """Detect if app is a Dash application."""
    cls_name = type(app).__name__
    if cls_name == "Dash":
        return True
    for base in type(app).__mro__:
        if base.__name__ == "Dash":
            return True
    server = getattr(app, "server", None)
    if server is not None and hasattr(server, "after_request"):
        if hasattr(app, "layout") or hasattr(app, "callback_map"):
            return True
    return False


def _enhance_web_app(app: Any, csp_policy: str | None = None) -> None:
    """v6.0: Enhanced with distributed tracing support."""
    is_dash = _is_dash_app(app)
    framework = "Dash" if is_dash else "Flask"

    effective_csp: str
    if csp_policy is not None:
        effective_csp = csp_policy
    elif is_dash:
        effective_csp = Security.CSP_DASH_COMPAT
    else:
        effective_csp = Security.CSP_STRICT

    try:
        server = getattr(app, "server", app)

        # ── Hook 1: Security Headers + Request Context ──
        @server.after_request
        def add_headers(response: Any) -> Any:
            headers = Security.security_headers(csp=effective_csp)
            for k, v in headers.items():
                response.headers[k] = v
            response.headers["X-Request-ID"] = RequestContext.trace_id()
            response.headers["X-Response-Time"] = f"{RequestContext.elapsed() * 1000:.1f}ms"
            return response

        # ── Hook 2: Trace ID + Timer (v6.0: 支持从 HTTP Header 提取) ──
        @server.before_request
        def trace_req() -> None:
            try:
                from flask import request
                # v6.0: 尝试从 Header 提取 trace_id
                for header in ("X-Trace-ID", "X-Request-ID", "X-Correlation-ID"):
                    val = request.headers.get(header, "")
                    if val:
                        _ctx_trace_id.set(val)
                        break
                if not _ctx_trace_id.get():
                    _ctx_trace_id.set(secrets.token_hex(8))
                _ctx_request_start.set(time.perf_counter())
            except ImportError:
                _ctx_trace_id.set(secrets.token_hex(8))
                _ctx_request_start.set(time.perf_counter())

        # ── Hook 3: Request Logging ──
        @server.after_request
        def log_req(response: Any) -> Any:
            ms = RequestContext.elapsed() * 1000
            try:
                from flask import request
                method = request.method
                path = request.path
                status = response.status_code

                if path.startswith("/_dash-component-suites/") or path.startswith("/assets/"):
                    if status >= 400:
                        log.warning("🌐 %s %s → %d (%.0fms)", method, path, status, ms)
                    return response

                if status >= 500:
                    log.error("🌐 %s %s → %d (%.0fms) 🔴", method, path, status, ms)
                elif status >= 400:
                    log.warning("🌐 %s %s → %d (%.0fms) 🟡", method, path, status, ms)
                elif ms > 500:
                    log.warning("🌐 %s %s → %d (%.0fms) 🐢", method, path, status, ms)
                else:
                    log.info("🌐 %s %s → %d (%.0fms)", method, path, status, ms)
            except ImportError:
                pass
            return response

        # ── Hook 4: Error Handler ──
        @server.errorhandler(Exception)
        def handle_error(exc: Exception) -> Any:
            status = getattr(exc, "code", 500)
            if not isinstance(status, int) or status < 400:
                status = 500

            info = ExceptionFingerprinter.capture(exc)
            tid = RequestContext.trace_id()

            log.error(
                "💥 HTTP %d | %s: %s | request_id=%s | fingerprint=%s",
                status, type(exc).__name__, exc, tid, info.get("fingerprint"),
                exc_info=True,
            )

            try:
                from flask import jsonify
                return jsonify({
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc) if status < 500 else "Internal Server Error",
                        "request_id": tid,
                        "fingerprint": info.get("fingerprint"),
                        "hint": f"Check server logs for request_id={tid}",
                    }
                }), status
            except ImportError:
                raise exc

        # ── Dash callback wrapping ──
        if is_dash:
            _wrap_dash_callbacks(app)

        log.info(
            "✅ Web app enhanced [%s] | CSP: %s | hooks: headers, trace, logging, errors%s",
            framework,
            effective_csp[:60] + ("..." if len(effective_csp) > 60 else ""),
            ", callback-wrapping" if is_dash else "",
        )

    except Exception as e:
        log.error(
            "❌ Web enhancement FAILED for %s app: %s\n"
            "   app type: %s\n"
            "   server attr: %s\n"
            "   csp_policy: %s",
            framework, e,
            type(app).__name__,
            type(getattr(app, "server", None)).__name__,
            effective_csp[:80],
            exc_info=True,
        )


def _wrap_dash_callbacks(app: Any) -> None:
    """Wrap Dash callbacks with error logging."""
    try:
        callback_map = getattr(app, "callback_map", None)
        if not callback_map:
            return

        wrapped_count = 0
        for output_key, cb_info in callback_map.items():
            original_func = cb_info.get("callback")
            if original_func is None:
                continue

            @functools.wraps(original_func)
            def _make_wrapper(orig: Any) -> Any:
                def wrapper(*args: Any, **kwargs: Any) -> Any:
                    try:
                        return orig(*args, **kwargs)
                    except Exception as exc:
                        info = ExceptionFingerprinter.capture(exc)
                        tid = RequestContext.trace_id()
                        log.error(
                            "💥 Dash callback error | %s: %s | request_id=%s | fingerprint=%s",
                            type(exc).__name__, exc, tid, info.get("fingerprint"),
                            exc_info=True,
                        )
                        raise
                return wrapper

            cb_info["callback"] = _make_wrapper(original_func)
            wrapped_count += 1

        if wrapped_count > 0:
            log.info("🔗 Wrapped %d Dash callbacks with error logging", wrapped_count)

    except Exception as e:
        log.warning(
            "⚠️ Dash callback wrapping failed (non-fatal): %s", e, exc_info=True,
        )


# ╔══════════════════════════════════════════════════╗
# ║  Part 8: Global Singletons & Bootstrap          ║
# ╚══════════════════════════════════════════════════╝

PERF = PerformanceMonitor()
EVENTS = EventBus()
METRICS = MetricsCollector()
AUDIT = AuditLog()
SHUTDOWN = GracefulShutdown()
SYSTEM = SystemMonitor()
HEALTH = HealthChecker()

_boost_initialized = False


def boost(
    app: Any = None,
    *,
    log_level: str = "INFO",
    memory_watchdog: bool = False,
    memory_limit_mb: float = 2048,
    graceful_shutdown: bool = True,
    rich_logging: bool = True,
    sanitize_logs: bool = True,
    exception_fingerprint: bool = True,
    csp_policy: str | None = None,
    json_log: bool | None = None,
) -> dict[str, Any]:
    """
    Bootstrap the runtime enhancement layer.

    v6.0:
      - 支持从 HTTP Header 自动提取 trace_id (X-Trace-ID)
      - json_log=True 启用 JSON 格式日志（✅ P0-2 修复：现在能真正生效）
      - 自动注册健康检查
    """
    global log, _boost_initialized, _LOG_JSON_FORMAT, _memory_limit_mb

    _memory_limit_mb = memory_limit_mb

    if _boost_initialized:
        log.debug("boost() already initialized, skipping")
        return {
            "_skipped": True,
            "log": log,
            "perf": PERF,
            "events": EVENTS,
        }

    # ── 解析 JSON 日志开关（优先级：参数 > 环境变量 > 当前值） ──
    if json_log is not None:
        _LOG_JSON_FORMAT = json_log
    elif os.getenv("GOOD_ADDONS_LOG_JSON", "false").lower() == "true":
        _LOG_JSON_FORMAT = True

    # ✅ P0-2 修复：每次调用 _setup_logging 都会判断是否需要重建 handler，
    #    因此 json_log 参数现在能真正生效。
    log = _setup_logging(
        level=log_level,
        rich=rich_logging,
        json_format=_LOG_JSON_FORMAT,
    )
    log.info("🚀 good_addons v%s bootstrapping...", __version__)

    if graceful_shutdown:
        SHUTDOWN.install()
    if memory_watchdog:
        SYSTEM.start_memory_watchdog(max_mb=memory_limit_mb)
    if exception_fingerprint:
        _install_exception_hook()
    if app:
        _enhance_web_app(app, csp_policy=csp_policy)

    _boost_initialized = True

    deps = [
        n for n, f in [
            ("rich", _HAS_RICH),
            ("psutil", _HAS_PSUTIL),
            ("orjson", _HAS_ORJSON),
            ("pydantic", _HAS_PYDANTIC),
            ("prometheus", _HAS_PROMETHEUS),
            ("crypto", _HAS_CRYPTO),
        ] if f
    ]
    log.info(
        "✅ boost() complete | deps: %s | pid: %d | json_log: %s",
        ", ".join(deps) or "stdlib",
        os.getpid(),
        _LOG_JSON_FORMAT,
    )

    return {
        "_skipped": False,
        "log": log,
        "perf": PERF,
        "events": EVENTS,
        "audit": AUDIT,
        "shutdown": SHUTDOWN,
        "system": SYSTEM,
        "health": HEALTH,
        "security": Security,
    }
# ╔══════════════════════════════════════════════════╗
# ║  Part 9: Diagnostics (v6.0 enhanced)            ║
# ╚══════════════════════════════════════════════════╝

def diag(*, perf_top: int = 15, audit_last: int = 20, include_health: bool = True) -> str:
    """
    v6.0: 增强诊断——集成健康检查输出。
    快速诊断函数，一键输出性能/审计/系统/事件总线/健康状态。
    """
    sections: list[str] = []

    # Performance
    sections.append("═══ Performance ═══")
    sections.append(PERF.format_table(top_n=perf_top))

    # Audit
    sections.append("\n═══ Recent Audit ═══")
    entries = AUDIT.query(last_n=audit_last)
    if entries:
        for e in entries:
            ts = e["timestamp"][:19]
            sections.append(f"  {ts} | {e['action']:<20} | {e['actor']:<15} | {str(e.get('details', ''))[:80]}")
    else:
        sections.append("  (empty)")

    # System
    sections.append("\n═══ System ═══")
    mem = SystemMonitor.memory_info()
    cpu = SystemMonitor.cpu_percent()
    if mem:
        sections.append(f"  RSS: {mem['rss_mb']:.1f} MB | VMS: {mem['vms_mb']:.1f} MB | CPU: {cpu:.1f}%")
    else:
        sections.append("  (psutil not available)")

    # Metrics
    sections.append("\n═══ Metrics ═══")
    metrics = METRICS.snapshot()
    if metrics:
        for k, v in sorted(metrics.items()):
            sections.append(f"  {k}: {v:.0f}")
    else:
        sections.append("  (empty)")

    # EventBus history (v6.0: 使用 get_history)
    sections.append("\n═══ Recent Events ═══")
    recent = EVENTS.get_history(last_n=10)
    if recent:
        for e in recent:
            ts = datetime.fromtimestamp(e["time"], tz=UTC).strftime("%H:%M:%S")
            sections.append(f"  {ts} | {e['event']:<25} | seq={e['seq']}")
    else:
        sections.append("  (empty)")

    # ── v6.0: Health Check ──
    if include_health:
        sections.append("\n" + HEALTH.format())

    output = "\n".join(sections)
    log.info("📊 Diagnostic dump:\n%s", output)
    return output
