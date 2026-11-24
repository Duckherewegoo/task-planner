# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.core — 框架无关的通用运行时增强内核                    ║
# ║  Pyright strict | PEP-8 | Python 3.9+                              ║
# ║  License: GPLv3                                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
good_addons.core —— 运行时增强层的"通用底座"。
═══════════════════════════════════════════════════════════════════════

本模块承载**与具体 Web 框架/服务器完全无关**的全部能力，是 support/
各适配器共同依赖的地基。设计上刻意不 import 任何第三方 Web 框架——
所有可选依赖（psutil / rich / orjson / pydantic / prometheus /
cryptography）均走 try-import + 优雅降级，缺任何一个都不影响 import。

分层职责（与 registry.py / support/ 的关系）：
    core.py      ── 通用增强：日志、脱敏、装饰器、异常指纹、事件总线、
                    性能监控、审计、指标、优雅退出、健康检查、安全工具、
                    JSON 快速编解码、RequestContext。
    registry.py  ── 只管"support/ 里有哪些适配器、各自叫什么名字"。
    support/*.py ── 只管"怎么给某一个框架/服务器打补丁"，延迟 import
                    对应框架，并回调本模块暴露的原语。

因此 support 适配器应 `from good_addons.core import (...)` 取用本模块
的单例与工具，而**不应**反向被本模块 import——依赖方向单向向下，
杜绝循环 import。

本模块对外暴露的"给 support 用的稳定接口"（语义冻结，勿随意改名）：
    ── 单例：PERF / EVENTS / METRICS / AUDIT / SHUTDOWN / SYSTEM / HEALTH
    ── 上下文：RequestContext / _ctx_trace_id / _ctx_request_start /
               _ctx_user_id / new_trace_id()
    ── 组件：Security / ExceptionFingerprinter / PerformanceMonitor /
             EventBus / HealthChecker / SystemMonitor / AuditLog /
             MetricsCollector / GracefulShutdown
    ── 工具：log / get_logger / set_log_level / fast_json_dumps /
             fast_json_loads / set_memory_limit_mb / _HAS_*
以下划线开头者为"包内私有"，仅 support/ 与 good.py 使用。
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
from collections import defaultdict, deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar, cast, overload

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Optional Dependencies (Graceful Degradation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 每个可选依赖只影响"用到它那部分"的增强，缺失时优雅降级而非报错。
# 扫描 support/ 时本模块即被 import，故此处绝不能引入任何硬失败。

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

__version__ = "7.0.0"

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Global Context Variables
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 用 contextvars 而非 threading.local：同一线程内 asyncio 多任务之间
# 也能正确隔离 trace_id，且 contextvars 会随 asyncio Task 自动传播。

_ctx_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_ctx_request_start: contextvars.ContextVar[float] = contextvars.ContextVar(
    "request_start", default=0.0
)
_ctx_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")
_ctx_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")


def new_trace_id() -> str:
    """生成一个短随机 trace_id（16 位 hex）。support 适配器统一调用它。"""
    return secrets.token_hex(8)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP Header Conventions (shared by all support adapters)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 把"用哪些 Header 传追踪 / 身份信息"这一约定收敛到 core，各 support
# 适配器（flask / fastapi / quart …）统一引用，避免每个适配器各写一套
# 字符串导致的行为漂移。

#: 入站：分布式追踪 ID（按优先级依次探测）。
TRACE_ID_HEADERS: tuple[str, ...] = ("X-Trace-ID", "X-Request-ID", "X-Correlation-ID")
#: 入站：可选的用户 ID / 会话 ID，用于审计与日志归属。
USER_ID_HEADER = "X-User-ID"
SESSION_ID_HEADER = "X-Session-ID"
#: 出站：回显给调用方的请求 ID。
REQUEST_ID_HEADER = "X-Request-ID"
#: 出站：服务端处理耗时（人类可读，如 "12.3ms"）。
RESPONSE_TIME_HEADER = "X-Response-Time"
#: 出站：服务端处理耗时（毫秒，纯数字，便于机器采集）。
RESPONSE_TIME_MS_HEADER = "X-Response-Time-Ms"


def raw_headers_from_environ(environ: dict[str, Any]) -> dict[str, str]:
    """从 WSGI environ 取出请求头，键统一成 **WSGI 大写写法**（``X_TRACE_ID``）。

    为什么不用 ``request.headers``：Werkzeug 会把头名标题化
    （X-Trace-ID → X-Trace-Id），无法与本库"严格区分大小写"的配置名比对。
    environ 里的头键有两种形态，这里都兼容：
      - 真实 WSGI 服务器：带 ``HTTP_`` 前缀（HTTP_X_TRACE_ID）；
      - Werkzeug 测试客户端 / 部分中间件：不带前缀（X_TRACE_ID）。
    下划线是 WSGI 对连字符的转义，因此 ``X-Trace-ID`` 与 ``X-Trace-IDs``
    得到不同键，名字写错依然取不到——区分大小写（及拼写）的哲学不变。
    """
    out: dict[str, str] = {}
    for key, val in environ.items():
        name = key[5:] if key.startswith("HTTP_") else key
        if name != key and not name.isupper():
            continue  # 带 HTTP_ 但并非头（如 HTTP2 之类），跳过
        if isinstance(val, str) and name.replace("_", "").isalnum():
            out[name] = val
    return out


def headers_from_asgi(
    headers: Any,
) -> dict[str, str]:
    """把 ASGI ``scope["headers"]``（[(b"x-trace-id", b"abc"), ...]）
    转成 core 的大写头键 dict，供 bind_context_from_headers 使用。

    ASGI 规范规定头名由服务器转成小写字节串，因此客户端的大小写变体
    天然归一；与 WSGI 路径一样，"区分大小写"约束的是配置里写的名字。
    兼容已解码的 [(str, str)] 与普通 dict 入参。
    """
    out: dict[str, str] = {}
    if isinstance(headers, dict):
        items = headers.items()
    else:
        items = headers
    for key, val in items:
        if isinstance(key, bytes):
            key = key.decode("latin-1")
        if isinstance(val, bytes):
            val = val.decode("latin-1")
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        out[key.upper().replace("-", "_")] = val
    return out


def _wsgi(name: str) -> str:
    """Header 规范名 → WSGI 头键：X-Trace-ID → X_TRACE_ID。"""
    return name.upper().replace("-", "_")


def bind_context_from_headers(
    headers: dict[str, str],
) -> str:
    """从 HTTP 请求头绑定请求上下文，返回生效的 trace_id。

    供各 support 适配器在请求入口（before_request / middleware）调用，
    把 Header 里的追踪与身份信息写入 contextvars：

      - trace_id：命中 TRACE_ID_HEADERS 中第一个非空值；都没有则新生成；
      - user_id / session_id：命中则写入对应 contextvar。

    入参必须是 raw_headers_from_environ(environ) 的产物（WSGI 键写法）；
    ASGI 适配器传入框架 headers 迭代出的 (name, value) 归一化 dict 亦可，
    但键必须大写下划线形式，见 _get。
    """
    import time as _time

    def _get(name: str) -> str:
        """按规范名精确取 Header（内部转 WSGI 键比对，区分大小写）。

        与区分大小写的文件系统同一哲学：名字就是名字，配置里写错
        （如 X-Trace-Idx）就是取不到，不替调用方猜；
        但客户端的大小写变体由 WSGI 层归一，不算写错。
        """
        val = headers.get(_wsgi(name), "")
        return str(val) if val else ""

    tid = next((_get(h) for h in TRACE_ID_HEADERS if _get(h)), "")
    if not tid:
        tid = new_trace_id()
    _ctx_trace_id.set(tid)

    uid = _get(USER_ID_HEADER)
    if uid:
        _ctx_user_id.set(uid)
    sid = _get(SESSION_ID_HEADER)
    if sid:
        _ctx_session_id.set(sid)

    _ctx_request_start.set(_time.perf_counter())
    return tid


# ╔══════════════════════════════════════════════════╗
# ║  Part 1: Core Infrastructure                    ║
# ╚══════════════════════════════════════════════════╝


class _SingletonMeta(type):
    """线程安全的单例元类。

    双重检查加锁（double-checked locking）：先用非加锁读快速放行，
    命中未初始化再进锁二次确认，避免并发下构造出多份实例。
    """

    _instances: dict[type, Any] = {}
    _lock = threading.Lock()

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class EventBus:
    """线程安全的进程内 Pub/Sub 事件总线。

    特性：
      - 按 priority 降序派发（大者先执行）；
      - 保留最近 1000 条事件历史供诊断；
      - 单个 handler 抛异常不会中断其余 handler；
      - 提供同步 emit 与异步 emit_async 两套入口。

    注意：handler 异常**有意**用 print 到 stderr 而非 logger——EventBus
    可能在日志系统初始化前就被调用，且若 logger 自身也订阅了事件，
    走 logger 会形成递归。
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[int, Callable[..., Any]]]] = defaultdict(list)
        self._lock = threading.RLock()
        self._history: deque[dict[str, Any]] = deque(maxlen=1000)
        self._seq = 0

    def on(
        self, event: str, handler: Callable[..., Any], *, priority: int = 0
    ) -> Callable[[], None]:
        """订阅事件，返回一次性取消订阅闭包。"""
        with self._lock:
            self._subs[event].append((priority, handler))
            self._subs[event].sort(key=lambda x: -x[0])
        return lambda: self.off(event, handler)

    def off(self, event: str, handler: Callable[..., Any]) -> None:
        """取消订阅；handler 不存在时静默忽略。"""
        with self._lock:
            self._subs[event] = [(p, h) for p, h in self._subs[event] if h is not handler]

    def emit(self, event: str, **kwargs: Any) -> list[Any]:
        """同步派发事件，返回各 handler 的返回值列表。"""
        with self._lock:
            handlers = list(self._subs.get(event, []))
            self._seq += 1
            seq = self._seq
            self._history.append(
                {"event": event, "kwargs": kwargs, "time": time.time(), "seq": seq}
            )
        results: list[Any] = []
        for _, handler in handlers:
            try:
                results.append(handler(**kwargs))
            except Exception as e:
                print(
                    f"[EventBus] Error in {getattr(handler, '__name__', handler)!r} "
                    f"for {event!r}: {e}",
                    file=sys.stderr,
                )
        return results

    async def emit_async(self, event: str, **kwargs: Any) -> list[Any]:
        """异步派发：协程 handler 被 await，普通 handler 直接调用。"""
        with self._lock:
            handlers = list(self._subs.get(event, []))
            self._seq += 1
            seq = self._seq
            self._history.append(
                {"event": event, "kwargs": kwargs, "time": time.time(), "seq": seq}
            )
        results: list[Any] = []
        for _, handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    results.append(await handler(**kwargs))
                else:
                    results.append(handler(**kwargs))
            except Exception as e:
                print(
                    f"[EventBus] Async error in {getattr(handler, '__name__', handler)!r} "
                    f"for {event!r}: {e}",
                    file=sys.stderr,
                )
        return results

    def get_history(self, last_n: int = 50) -> list[dict[str, Any]]:
        """返回最近 N 条事件历史（供诊断，替代直接访问 _history）。"""
        with self._lock:
            return list(self._history)[-last_n:]

    def subscriber_count(self) -> int:
        """当前总订阅数量（跨全部事件名）。"""
        with self._lock:
            return sum(len(v) for v in self._subs.values())


class RequestContext:
    """请求级上下文管理（async / 线程安全）。

    基于 contextvars，故：

      - 同一线程多个请求互不串味；
      - asyncio 多个 Task 各自持有独立快照，且子 Task 自动继承父上下文。

    典型用法（support 适配器在请求入口开 scope）：

        with RequestContext.scope(trace_id=..., user_id=...) as ctx:
            ...
    """

    #: 约定俗成的分布式追踪 Header，按优先级依次探测。
    _trace_id_headers = ("X-Trace-ID", "X-Request-ID", "X-Correlation-ID")

    @staticmethod
    @contextmanager
    def scope(trace_id: str = "", user_id: str = "", **extra: Any) -> Generator[dict[str, Any]]:
        """开启一个请求作用域，退出时自动 reset contextvar（即便异常）。"""
        tid = trace_id or new_trace_id()
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
        """从 HTTP Header 提取 trace_id 并写入上下文；未命中返回空串。

        精确匹配 TRACE_ID_HEADERS 的规范写法，不忽略大小写。
        """
        for header in RequestContext._trace_id_headers:
            val = headers.get(header, "")
            if val:
                _ctx_trace_id.set(str(val))
                return str(val)
        return ""

    @staticmethod
    def trace_id() -> str:
        return _ctx_trace_id.get()

    @staticmethod
    def user_id() -> str:
        return _ctx_user_id.get()

    @staticmethod
    def elapsed() -> float:
        """自请求开始至今的秒数；未开启 scope 时返回 0。"""
        start = _ctx_request_start.get()
        return time.perf_counter() - start if start else 0.0

    @staticmethod
    def snapshot() -> dict[str, Any]:
        """当前上下文快照，用于结构化日志 / 异常报告。"""
        return {
            "trace_id": RequestContext.trace_id(),
            "user_id": RequestContext.user_id(),
            "elapsed_ms": round(RequestContext.elapsed() * 1000, 2),
        }


class PerformanceMonitor:
    """全局性能采集器：统计调用次数、均值与 p50/p95/p99。

    每个被观测名维护一个定长（5000）耗时滑窗用于分位数计算；
    样本不足 2 条的名字不进入快照（分位数无意义）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, dict[str, Any]] = {}

    def record(self, name: str, duration_ms: float, *, error: bool = False) -> None:
        with self._lock:
            if name not in self._stats:
                self._stats[name] = {
                    "count": 0,
                    "errors": 0,
                    "total_ms": 0.0,
                    "times": deque(maxlen=5000),
                }
            s = self._stats[name]
            s["count"] += 1
            s["total_ms"] += duration_ms
            s["times"].append(duration_ms)
            if error:
                s["errors"] += 1

    def snapshot(self, *, top_n: int = 0, sort_by: str = "total_ms") -> list[dict[str, Any]]:
        """返回按 sort_by 降序的统计快照；top_n=0 表示不截断。"""
        with self._lock:
            items: list[dict[str, Any]] = []
            for name, s in self._stats.items():
                times = list(s["times"])
                if len(times) < 2:
                    continue
                try:
                    q = statistics.quantiles(times, n=100)
                    p50, p95, p99 = q[49], q[94], q[98]
                except Exception:
                    p50 = p95 = p99 = times[0]
                items.append(
                    {
                        "name": name,
                        "calls": s["count"],
                        "errors": s["errors"],
                        "total_ms": round(s["total_ms"], 2),
                        "avg_ms": round(s["total_ms"] / s["count"], 2),
                        "p50_ms": round(p50, 2),
                        "p95_ms": round(p95, 2),
                        "p99_ms": round(p99, 2),
                        "max_ms": round(max(times), 2),
                    }
                )
            items.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
            return items[:top_n] if top_n else items

    def format_table(self, *, top_n: int = 20) -> str:
        """渲染成等宽文本表格，供 diag() 直接打印。"""
        items = self.snapshot(top_n=top_n)
        if not items:
            return "(no data)"
        header = (
            f"{'Function':<40} {'Calls':>7} {'Avg':>8} {'P50':>8} {'P95':>8} {'P99':>8} {'Err':>5}"
        )
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

#: 敏感信息脱敏规则：(正则, 替换)。依次对日志消息做 sub。
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*\S+", re.I
        ),
        r"\1=***",
    ),
    (re.compile(r"\b\d{17}[\dXx]\b"), "***ID_CARD***"),
    (
        re.compile(
            r"\b(?:Bearer\s+)?[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{20,}\b"
        ),
        "***JWT***",
    ),
]

#: 结构化（JSON）日志开关。环境变量可被 boost(json_log=...) 覆盖。
_LOG_JSON_FORMAT = os.getenv("GOOD_ADDONS_LOG_JSON", "false").lower() == "true"


class _ContextFilter(logging.Filter):
    """把 trace_id / user_id 注入每条 LogRecord，供 Formatter 引用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _ctx_trace_id.get() or "-"  # type: ignore[attr-defined]
        record.user_id = _ctx_user_id.get() or "-"  # type: ignore[attr-defined]
        return True


class _SanitizeFilter(logging.Filter):
    """对日志正文做敏感信息脱敏（就地改写 msg 并清空 args）。"""

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
    """结构化 JSON 日志格式化器，便于被 ELK / Loki 采集。"""

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
    """初始化（或重建）good_addons logger。

    可重复调用且幂等：若 handler 已存在且目标格式未变，仅调整级别；
    若 JSON/文本格式发生变化，则清空旧 handler 重建——这样
    ``boost(json_log=True)`` 能真正生效，而不会被 import 期的旧 handler 吃掉。
    """
    logger = logging.getLogger("good_addons")

    target_json = json_format if json_format is not None else _LOG_JSON_FORMAT
    current_json = getattr(logger, "_log_json_format", None)

    if logger.handlers and current_json == target_json:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        return logger

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

    if target_json:
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_json_formatter)
    elif rich and _HAS_RICH and Console is not None and RichHandler is not None:
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
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s │ %(levelname)-7s │ %(trace_id)s │ %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    handler.addFilter(_ctx_filter)
    handler.addFilter(_sanitize_filter)
    logger.addHandler(handler)
    logger._log_json_format = target_json  # type: ignore[attr-defined]

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stderr))
    root.addFilter(_ctx_filter)
    root.addFilter(_sanitize_filter)

    return logger


log: logging.Logger = _setup_logging()


def get_logger(name: str = "") -> logging.Logger:
    """获取一个带上下文注入与脱敏过滤的 logger。"""
    logger = logging.getLogger(name or "good_addons")
    logger.addFilter(_ctx_filter)
    logger.addFilter(_sanitize_filter)
    return logger


def set_log_level(level: str | int) -> None:
    """运行时安全地调整日志级别（root + good_addons 同时生效）。"""
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(level)
    logging.getLogger("good_addons").setLevel(level)
    log.info("📊 Log level changed to %s", logging.getLevelName(level))


# ╔══════════════════════════════════════════════════╗
# ║  Part 3: Exception Fingerprinting               ║
# ╚══════════════════════════════════════════════════╝


class ExceptionFingerprinter:
    """为异常生成稳定指纹（12 位 md5）并在时间窗内去重。

    指纹只取异常类型、消息与归一化的栈尾 3 帧，故同一根因的重复异常
    会得到相同指纹，便于日志聚合与告警降噪。
    """

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
        """返回 ``{"fingerprint": ..., "deduplicated": bool, ...}``。

        窗口内重复出现时只回指纹并标记 deduplicated；首次出现会附带
        请求上下文快照并写入审计日志。
        """
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
        AUDIT.record(
            "exception",
            details={
                "fingerprint": fp,
                "type": type(exc).__name__,
                "message": str(exc)[:500],
                "context": snapshot,
            },
        )
        return {"fingerprint": fp, "deduplicated": False, "context": snapshot}


def _install_exception_hook() -> None:
    """安装全局 excepthook：未捕获异常先做指纹去重 + 结构化日志，再交还原 hook。"""
    original_hook = sys.excepthook

    def _hook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        try:
            info = ExceptionFingerprinter.capture(exc_value)
            if not info.get("deduplicated"):
                log.error(
                    "💥 Unhandled exception [%s] | ctx=%s",
                    info["fingerprint"],
                    info.get("context"),
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
    """安全地生成参数 repr，超长截断、失败兜底，绝不因日志拖垮业务。"""
    try:
        parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
        res = ", ".join(parts)
        return res[:max_len] + "..." if len(res) > max_len else res
    except Exception:
        return "<repr failed>"


@overload
def trace(func: F) -> F: ...
@overload
def trace(
    *,
    level: int = logging.DEBUG,
    slow_ms: float = 200,
    log_args: bool = False,
    memory: bool = False,
    name: str = "",
) -> Callable[[F], F]: ...


def trace(
    _func: F | None = None,
    *,
    level: int = logging.DEBUG,
    slow_ms: float = 200,
    log_args: bool = False,
    memory: bool = False,
    name: str = "",
) -> F | Callable[[F], F]:
    """全功能追踪装饰器：计时 + 慢调用告警 + 失败计数 + 可选参数/内存记录。

    自动识别同步与协程函数。耗时统一在 finally 计算一次，成功/失败都写入
    PerformanceMonitor；超过 slow_ms 打 WARNING；memory=True 时额外报告 RSS 增量。
    """

    def decorator(func: F) -> F:
        fname = name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            mem_before = (
                psutil.Process().memory_info().rss if memory and _HAS_PSUTIL and psutil else 0
            )
            old = _ctx_trace_id.set(_ctx_trace_id.get() or new_trace_id())
            recorded = False
            try:
                if log_args:
                    log.log(level, "→ %s(%s)", fname, _safe_repr(args, kwargs))
                return await func(*args, **kwargs)
            except Exception:
                ms = (time.perf_counter() - t0) * 1000
                PERF.record(fname, ms, error=True)
                log.error("✗ %s FAILED %.1fms", fname, ms)
                recorded = True
                raise
            finally:
                _ctx_trace_id.reset(old)
                ms = (time.perf_counter() - t0) * 1000
                if not recorded:
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
            mem_before = (
                psutil.Process().memory_info().rss if memory and _HAS_PSUTIL and psutil else 0
            )
            old = _ctx_trace_id.set(_ctx_trace_id.get() or new_trace_id())
            recorded = False
            try:
                if log_args:
                    log.log(level, "→ %s(%s)", fname, _safe_repr(args, kwargs))
                return func(*args, **kwargs)
            except Exception:
                ms = (time.perf_counter() - t0) * 1000
                PERF.record(fname, ms, error=True)
                log.error("✗ %s FAILED %.1fms", fname, ms)
                recorded = True
                raise
            finally:
                _ctx_trace_id.reset(old)
                ms = (time.perf_counter() - t0) * 1000
                if not recorded:
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


@overload
def timed(func: F) -> F: ...
@overload
def timed(*, slow_ms: float = 200, name: str = "") -> Callable[[F], F]: ...


def timed(
    _func: F | None = None,
    *,
    slow_ms: float = 200,
    name: str = "",
) -> F | Callable[[F], F]:
    """轻量计时装饰器（@trace 的精简版）：只记耗时 + 慢调用告警，不记参数/内存。"""

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


@overload
def fallback(func: F) -> F: ...
@overload
def fallback(
    *, value: Any = None, catch: tuple[type[Exception], ...] = (Exception,), log_error: bool = True
) -> Callable[[F], F]: ...


def fallback(
    _func: F | None = None,
    *,
    value: Any = None,
    catch: tuple[type[Exception], ...] = (Exception,),
    log_error: bool = True,
) -> F | Callable[[F], F]:
    """优雅降级装饰器：命中 catch 的异常被吞掉并返回备用值，不向外抛。

    适用于非关键路径；``value`` 可传可调用对象，每次失败时求值。

    Example::

        @fallback(value=list)
        def fetch() -> list[str]:
            return api_call()  # 失败时返回 []
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
def retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential: bool = True,
    jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    backoff_factor: float = 2.0,
) -> Callable[[F], F]: ...


def retry(
    _func: F | None = None,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential: bool = True,
    jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    backoff_factor: float = 2.0,
) -> F | Callable[[F], F]:
    """指数退避重试装饰器。最后一次尝试仍失败则向上抛出原异常。"""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def decorator(func: F) -> F:
        def _delay(attempt: int) -> float:
            d = base_delay * (backoff_factor ** (attempt - 1)) if exponential else base_delay
            d = min(d, max_delay)
            if jitter:
                d *= 0.5 + secrets.randbelow(100) / 100.0
            return d

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt >= max_attempts:
                        log.error(
                            "✗ %s failed after %d attempts: %s", func.__qualname__, max_attempts, e
                        )
                        raise
                    delay = _delay(attempt)
                    log.warning(
                        "↻ %s attempt %d/%d failed (%s), retry in %.2fs",
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
            raise AssertionError("unreachable")  # 仅为类型收窄，逻辑上不会到达

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt >= max_attempts:
                        log.error(
                            "✗ %s failed after %d attempts: %s", func.__qualname__, max_attempts, e
                        )
                        raise
                    delay = _delay(attempt)
                    log.warning(
                        "↻ %s attempt %d/%d failed (%s), retry in %.2fs",
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        e,
                        delay,
                    )
                    time.sleep(delay)
            raise AssertionError("unreachable")

        if inspect.iscoroutinefunction(func):
            return cast(F, async_wrapper)
        return cast(F, sync_wrapper)

    if _func is not None:
        return decorator(_func)
    return decorator


class CircuitState(enum.Enum):
    """断路器三态。"""

    CLOSED = "closed"  # 正常放行
    OPEN = "open"  # 熔断，快速失败 / 走 fallback
    HALF_OPEN = "half_open"  # 试探恢复中


class CircuitOpenError(Exception):
    """断路器处于 OPEN 且未配置 fallback 时抛出。"""


class CircuitBreaker:
    """经典三态熔断器。

    CLOSED 下连续失败达 failure_threshold 次转 OPEN；OPEN 持续
    recovery_timeout 秒后转 HALF_OPEN；HALF_OPEN 下连续成功
    success_threshold 次转回 CLOSED，期间任一次失败立刻回到 OPEN。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        fallback: Callable[..., Any] | None = None,
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
        """读取当前状态；顺带在超时后惰性完成 OPEN → HALF_OPEN 迁移。"""
        with self._lock:
            if self._state == CircuitState.OPEN and (
                time.time() - self._last_fail_time >= self.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._successes = 0
                log.info("⚡ CircuitBreaker: OPEN → HALF_OPEN")
            return self._state

    def status(self) -> dict[str, Any]:
        """导出状态快照，供监控 / 健康检查使用。"""
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
def circuit_breaker(
    *,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    fallback: Callable[..., Any] | None = None,
) -> Callable[[F], F]: ...


def circuit_breaker(
    _func: F | None = None,
    *,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    fallback: Callable[..., Any] | None = None,
) -> F | Callable[[F], F]:
    """@circuit_breaker 语法糖：为每个被装饰函数创建一个独立断路器实例。"""
    breaker = CircuitBreaker(failure_threshold, recovery_timeout, fallback=fallback)
    if _func is not None:
        return breaker(_func)
    return breaker


class _TokenBucket:
    """线程安全的令牌桶（限流原语）。按经过时间补桶，容量封顶。"""

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
    """超出限流阈值且未配置 on_limit 回调时抛出。"""


def rate_limited(
    rate: float = 10.0, capacity: int = 20, *, on_limit: Callable[..., Any] | None = None
) -> Callable[[F], F]:
    """令牌桶限流装饰器。超限则调用 on_limit 或抛 RateLimitError。

    注意：桶为装饰器闭包私有，即"每个被装饰函数一个桶"。
    """

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


def cached(
    ttl: float = 300,
    *,
    maxsize: int = 1024,
    key_func: Callable[..., str] | None = None,
    jitter: float = 0.0,
) -> Callable[[F], F]:
    """带 TTL 与容量上限的线程安全缓存装饰器。

    满容时淘汰最早过期（expire-at 最小）的条目；jitter>0 时为每条 TTL
    叠加随机抖动，缓解缓存雪崩。附带 cache_clear() / cache_stats()。
    """

    def decorator(func: F) -> F:
        store: dict[str, tuple[Any, float]] = {}
        lock = threading.Lock()
        stats = {"hits": 0, "misses": 0, "evictions": 0}

        def _key(args: Any, kwargs: Any) -> str:
            if key_func:
                return key_func(*args, **kwargs)
            return hashlib.md5(
                str((func.__qualname__, repr(args), repr(sorted(kwargs.items())))).encode()
            ).hexdigest()

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _key(args, kwargs)
            now = time.time()
            with lock:
                hit = store.get(key)
                if hit is not None and now < hit[1]:
                    stats["hits"] += 1
                    return hit[0]
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

        wrapper.cache_clear = lambda: store.clear()  # type: ignore[attr-defined]
        wrapper.cache_stats = lambda: dict(stats)  # type: ignore[attr-defined]
        return cast(F, wrapper)

    return decorator


def memoize(func: F) -> F:
    """无过期、无上限的永久记忆化（幂等纯函数场景）。附带 cache_clear()。"""
    cache: dict[str, Any] = {}

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        key = hashlib.md5(
            str((func.__qualname__, repr(args), repr(sorted(kwargs.items())))).encode()
        ).hexdigest()
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]

    wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
    return cast(F, wrapper)


def validated(**type_hints: Any) -> Callable[[F], F]:
    """入参类型强制/校验装饰器。

    对声明的参数做 isinstance 检查，不匹配则尝试用目标类型构造转换；
    转换失败抛 TypeError。缺 pydantic 或未声明任何约束时直接透传原函数，
    保持零开销。
    """

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
                        raise TypeError(
                            f"Param '{name}': expected {expected.__name__}, "
                            f"got {type(bound.arguments[name]).__name__} ({e})"
                        ) from e
            return func(*bound.args, **bound.kwargs)

        return cast(F, wrapper)

    return decorator


@overload
def guarded(func: F) -> F: ...
@overload
def guarded(
    *,
    fallback: Any = None,
    catch: tuple[type[Exception], ...] = (Exception,),
    log_error: bool = True,
    reraise: bool = False,
) -> Callable[[F], F]: ...


def guarded(
    _func: F | None = None,
    *,
    fallback: Any = None,
    catch: tuple[type[Exception], ...] = (Exception,),
    log_error: bool = True,
    reraise: bool = False,
) -> F | Callable[[F], F]:
    """通用保护装饰器：捕获异常、记日志、返回兜底值或按 reraise 重抛。

    与 @fallback 的区别：本装饰器默认记录完整 traceback（exc_info=True），
    并可选择在记录后再抛出（reraise=True），适合"观测但不吞"的场景。
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except catch as e:
                if log_error:
                    log.error(
                        "🛡 %s caught %s: %s", func.__qualname__, type(e).__name__, e, exc_info=True
                    )
                if reraise:
                    raise
                return fallback() if callable(fallback) else fallback

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except catch as e:
                if log_error:
                    log.error(
                        "🛡 %s caught %s: %s", func.__qualname__, type(e).__name__, e, exc_info=True
                    )
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
    """防抖装饰器：连续调用只在最后一次之后 wait 秒真正执行一次。

    返回 None；基于 threading.Timer，适合配置热更新、UI 触发等场景。
    """

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
    """安全工具集：转义、XSS 剥离、SQL 注入探测、口令哈希、对称加密、安全响应头。"""

    _XSS = [
        re.compile(r"<script[^>]*>.*?</script>", re.I | re.S),
        re.compile(r"javascript\s*:", re.I),
        re.compile(r"on\w+\s*=", re.I),
    ]
    _SQL = re.compile(
        r"\b(UNION|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC|TRUNCATE|--|;)\b", re.I
    )

    #: CSP 预设：严格策略（纯 API / 非 Dash 场景默认）。
    CSP_STRICT = "default-src 'self'"
    #: CSP 预设：Dash 兼容策略（放宽 script/style inline + ws + plotly CDN）。
    CSP_DASH_COMPAT = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss: https://dash-version.plotly.com;"
    )

    @staticmethod
    def sanitize_html(text: str) -> str:
        """HTML 转义（转义引号），用于把不可信文本安全嵌入页面。"""
        return html.escape(str(text), quote=True)

    @staticmethod
    def strip_xss(text: str) -> str:
        """粗粒度剥离常见 XSS 向量（script 标签 / javascript: / 内联事件）。"""
        res = str(text)
        for p in Security._XSS:
            res = p.sub("", res)
        return res

    @staticmethod
    def detect_sql_injection(text: str) -> bool:
        """启发式检测 SQL 注入特征；命中返回 True（宁可误报不可漏报）。"""
        return bool(Security._SQL.search(str(text)))

    @staticmethod
    def hash_password(password: str, *, salt: str = "") -> str:
        """salt + SHA-256 口令哈希，返回 ``salt:hexdigest``；salt 缺省随机生成。"""
        salt = salt or secrets.token_hex(16)
        return f"{salt}:{hashlib.sha256(f'{salt}{password}'.encode()).hexdigest()}"

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """恒定时间校验口令，抵御时序侧信道。"""
        try:
            salt, expected = hashed.split(":", 1)
            return secrets.compare_digest(
                hashlib.sha256(f"{salt}{password}".encode()).hexdigest(), expected
            )
        except ValueError:
            return False

    @staticmethod
    def encrypt(data: str, key: bytes | None = None) -> tuple[str, bytes]:
        """Fernet 对称加密；key 缺省则生成。缺 cryptography 时抛 ImportError。"""
        if not _HAS_CRYPTO or not Fernet:
            raise ImportError("pip install cryptography")
        key = key or Fernet.generate_key()
        return Fernet(key).encrypt(data.encode()).decode(), key

    @staticmethod
    def decrypt(token: str, key: bytes) -> str:
        """Fernet 对称解密。缺 cryptography 时抛 ImportError。"""
        if not _HAS_CRYPTO or not Fernet:
            raise ImportError("pip install cryptography")
        return Fernet(key).decrypt(token.encode()).decode()

    @staticmethod
    def security_headers(csp: str | None = None) -> dict[str, str]:
        """返回一组基线安全响应头；csp=None 时用严格 CSP。"""
        return {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Content-Security-Policy": csp or Security.CSP_STRICT,
        }


class SystemMonitor:
    """进程级系统资源监控（依赖 psutil，缺失时优雅返回空值）。"""

    @staticmethod
    def memory_info() -> dict[str, Any]:
        """返回当前进程 RSS / VMS（MB）；无 psutil 时返回空 dict。"""
        if _HAS_PSUTIL and psutil:
            mem = psutil.Process().memory_info()
            return {"rss_mb": round(mem.rss / 1048576, 2), "vms_mb": round(mem.vms / 1048576, 2)}
        return {}

    @staticmethod
    def cpu_percent() -> float:
        """当前进程 CPU 百分比；无 psutil 时返回 0.0。"""
        if _HAS_PSUTIL and psutil:
            return psutil.Process().cpu_percent(interval=0.1)
        return 0.0

    @staticmethod
    def start_memory_watchdog(
        *,
        max_mb: float = 2048,
        interval: float = 30,
        callback: Callable[..., Any] | None = None,
    ) -> threading.Thread:
        """启动后台守护线程周期性检查 RSS，超阈值时告警 + 发 memory_critical 事件。

        返回已启动的 Thread（daemon，随主进程退出）。
        """

        def _watchdog() -> None:
            while True:
                time.sleep(interval)
                info = SystemMonitor.memory_info()
                if info.get("rss_mb", 0) > max_mb:
                    log.critical("🚨 Memory watchdog: %.0fMB > %.0fMB", info["rss_mb"], max_mb)
                    if callback:
                        try:
                            callback(info)
                        except Exception as cb_err:  # 回调异常不得拖垮看门狗线程
                            log.error("Memory watchdog callback error: %s", cb_err)
                    EVENTS.emit("memory_critical", **info)

        t = threading.Thread(target=_watchdog, daemon=True, name="mem-watchdog")
        t.start()
        return t


class AuditLog:
    """线程安全的审计日志环形缓冲（默认最多 10000 条）。"""

    def __init__(self, *, max_entries: int = 10000) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def record(
        self, action: str, *, actor: str = "", resource: str = "", details: Any = None
    ) -> dict[str, Any]:
        """写入一条审计记录，自动补全时间 / actor / trace_id。"""
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
        """按 action / actor 过滤，返回最近 last_n 条。"""
        with self._lock:
            entries = list(self._entries)
        if action:
            entries = [e for e in entries if e["action"] == action]
        if actor:
            entries = [e for e in entries if e["actor"] == actor]
        return entries[-last_n:]


class MetricsCollector:
    """指标收集器：内置简单计数，可选对接 Prometheus Counter。"""

    def __init__(self) -> None:
        self._counters: dict[str, Any] = {}
        self._simple: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def counter(self, name: str) -> None:
        """自增命名计数器；安装 prometheus_client 时同步暴露同名 Counter。"""
        with self._lock:
            self._simple[name] += 1
            if _HAS_PROMETHEUS and prometheus_client:
                if name not in self._counters:
                    self._counters[name] = prometheus_client.Counter(name, f"Counter: {name}")
                self._counters[name].inc()

    def snapshot(self) -> dict[str, float]:
        """导出简单计数器快照。"""
        with self._lock:
            return dict(self._simple)


class GracefulShutdown:
    """优雅退出管理器：注册按优先级排序的清理钩子，收到 SIGTERM/SIGINT 时依次执行。

    在 Werkzeug reloader 子进程（WERKZEUG_RUN_MAIN=true）中自动跳过安装，
    避免重复注册信号处理器。
    """

    def __init__(self) -> None:
        self._handlers: list[tuple[int, Callable[..., Any]]] = []
        self._event = threading.Event()
        self._installed = False

    def register(self, handler: Callable[..., Any], *, priority: int = 0) -> None:
        """注册退出钩子，priority 大者先执行。"""
        self._handlers.append((priority, handler))
        self._handlers.sort(key=lambda x: -x[0])

    def install(self) -> None:
        """安装信号处理器（幂等；reloader 子进程中跳过）。"""
        if self._installed or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            return
        self._installed = True

        def _handler(signum: int, frame: Any) -> None:
            try:
                log.info("🛑 Received signal %s, shutting down...", signal.Signals(signum).name)
            except ValueError:
                log.info("🛑 Received signal %s, shutting down...", signum)
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
        """阻塞等待退出事件；超时返回 False。"""
        return self._event.wait(timeout)


# ╔══════════════════════════════════════════════════╗
# ║  Part 6: HealthChecker                          ║
# ╚══════════════════════════════════════════════════╝


class HealthChecker:
    """统一健康检查组件：聚合各子系统的健康探针，输出结构化报告。

    关键修复（相对早期版本）：

      - ``run()`` 在已有运行中事件循环的上下文里不再死锁——检测到
        运行中的 loop 时，协程探针降级为 degraded 并提示改用
        ``await run_async()``，而非阻塞式 asyncio.run；
      - ``register()`` 同时支持"装饰器"与"直接调用"两种用法。

    整体状态聚合规则：任一探针 down → 整体 down；否则任一 degraded →
    degraded；全 ok 才 ok。
    """

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[], Any]] = {}
        self._lock = threading.Lock()

    @overload
    def register(
        self, name: str
    ) -> Callable[[Callable[[], dict[str, Any]]], Callable[[], dict[str, Any]]]: ...
    @overload
    def register(
        self, name: str, check_fn: Callable[[], dict[str, Any]]
    ) -> Callable[[], dict[str, Any]]: ...

    def register(
        self,
        name: str,
        check_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> (
        Callable[[Callable[[], dict[str, Any]]], Callable[[], dict[str, Any]]]
        | Callable[[], dict[str, Any]]
    ):
        """注册健康探针。

        用法 1（装饰器）::

            @HEALTH.register("db")
            def check_db() -> dict[str, Any]:
                return {"status": "ok"}

        用法 2（直接调用）::

            HEALTH.register("db", check_db)
        """

        def _do_register(fn: Callable[[], dict[str, Any]]) -> Callable[[], dict[str, Any]]:
            with self._lock:
                self._checks[name] = fn
            log.debug("🩺 HealthCheck registered: %s", name)
            return fn  # 返回原函数，不破坏装饰器链

        if check_fn is None:
            return _do_register
        return _do_register(check_fn)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._checks.pop(name, None)

    def _snapshot_checks(self) -> dict[str, Callable[[], Any]]:
        with self._lock:
            return dict(self._checks)

    @staticmethod
    def _normalize_result(result: Any) -> dict[str, Any]:
        """把任意探针返回值规范成 ``{"status": ..., ...}``。"""
        if not isinstance(result, dict):
            return {"status": "degraded", "value": result}
        result.setdefault("status", "ok")
        return result

    @staticmethod
    def _aggregate_overall(results: dict[str, dict[str, Any]]) -> str:
        overall = "ok"
        for r in results.values():
            st = r.get("status")
            if st == "down":
                return "down"
            if st == "degraded" and overall != "down":
                overall = "degraded"
        return overall

    def run(self, timeout: float = 5.0) -> dict[str, Any]:
        """同步运行全部健康探针。

        在已有运行中事件循环的上下文里，协程探针不会死锁，而是返回
        degraded 并提示改用 ``await run_async()``。
        """
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
                    results[name] = {
                        "status": "degraded",
                        "error": (
                            "async check skipped in sync context "
                            "(running loop detected); use `await HEALTH.run_async()`"
                        ),
                    }
                    continue
                try:
                    result = asyncio.run(asyncio.wait_for(fn(), timeout=timeout))
                except TimeoutError:
                    result = {"status": "down", "error": f"timeout after {timeout}s"}
                except Exception as e:
                    result = {"status": "down", "error": str(e)}
            else:
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
        """异步版本，推荐在 asyncio 上下文（async 端点 / startup）使用。"""
        results: dict[str, dict[str, Any]] = {}
        for name, fn in self._snapshot_checks().items():
            try:
                if inspect.iscoroutinefunction(fn):
                    result = await asyncio.wait_for(fn(), timeout=timeout)
                else:
                    result = fn()
            except TimeoutError:
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
        """同步健康报告文本。异步上下文中异步探针会显示为 degraded。"""
        report = self.run(timeout)
        return _format_health_report(report)

    async def format_async(self, timeout: float = 5.0) -> str:
        """异步版本健康报告文本。"""
        report = await self.run_async(timeout)
        return _format_health_report(report)


def _format_health_report(report: dict[str, Any]) -> str:
    """把健康报告 dict 渲染成多行文本（format / format_async 共用）。"""
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


# ╔══════════════════════════════════════════════════╗
# ║  Part 7: JSON Codec                             ║
# ╚══════════════════════════════════════════════════╝


def fast_json_dumps(obj: Any, *, pretty: bool = False) -> str:
    """快速 JSON 序列化：优先 orjson，回退标准库。

    额外处理 datetime / set / 任意对象（__dict__ / str 兜底），
    保证日志与响应体序列化不因特殊类型报错。
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
    return json.dumps(
        obj, default=_default_encoder, ensure_ascii=False, indent=2 if pretty else None
    )


def fast_json_loads(data: str | bytes) -> Any:
    """快速 JSON 反序列化：优先 orjson，回退标准库。"""
    if _HAS_ORJSON and orjson:
        return orjson.loads(data.encode() if isinstance(data, str) else data)
    return json.loads(data)


# ╔══════════════════════════════════════════════════╗
# ║  Part 8: Global Singletons                      ║
# ╚══════════════════════════════════════════════════╝

PERF = PerformanceMonitor()
EVENTS = EventBus()
METRICS = MetricsCollector()
AUDIT = AuditLog()
SHUTDOWN = GracefulShutdown()
SYSTEM = SystemMonitor()
HEALTH = HealthChecker()

#: 内存告警阈值（MB）。由 boost(memory_limit_mb=...) 通过 set_memory_limit_mb 覆盖，
#: 系统健康探针据此按 90% 水位判定 degraded——阈值单一来源，杜绝硬编码不一致。
_memory_limit_mb: float = 2048.0


def set_memory_limit_mb(limit_mb: float) -> None:
    """更新全局内存阈值（供 boost 调用），健康探针即时读到新值。"""
    global _memory_limit_mb
    _memory_limit_mb = limit_mb


@HEALTH.register("system")
def _system_health() -> dict[str, Any]:
    """系统健康探针：RSS 超过阈值 90% 即 degraded。"""
    mem = SystemMonitor.memory_info()
    cpu = SystemMonitor.cpu_percent()
    rss = mem.get("rss_mb", 0)
    status = "degraded" if rss > _memory_limit_mb * 0.9 else "ok"
    return {
        "status": status,
        "details": f"RSS: {rss:.1f}MB / limit {_memory_limit_mb:.0f}MB, CPU: {cpu:.1f}%",
    }


@HEALTH.register("eventbus")
def _eventbus_health() -> dict[str, Any]:
    """事件总线健康探针：经公开 API 读取订阅数与历史长度。"""
    history = EVENTS.get_history(last_n=1000)
    subs_count = EVENTS.subscriber_count()
    return {
        "status": "ok",
        "details": f"subscribers: {subs_count}, history: {len(history)}",
    }
