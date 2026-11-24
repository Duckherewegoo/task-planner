# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons — 企业级 Python 运行时增强层（模块化重构版 v7.0）     ║
# ║  Pyright strict | PEP-8 | Python 3.9+                             ║
# ║  License: GPLv3                                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
good_addons —— 企业级 Python 运行时增强层。
═══════════════════════════════════════════════════════════════════════

复制到任何项目即可 ``boost()``。相比 v6.2 单文件版，v7.0 做了**纯结构
重构**（行为语义保持兼容），把 2197 行单文件拆成职责单一的三层：

    good_addons/
      __init__.py    本文件：公开 API 门面（原 __all__ 全量 re-export）
      good.py        boost() 门面 + 通用增强装配 + Dash 应用层正交钩子
      registry.py    极简扫描器：support/<name>_support.py → "<name>"
      core.py        框架无关的通用增强内核（日志/脱敏/装饰器/异常指纹/
                     事件总线/性能监控/审计/指标/优雅退出/健康检查/安全/
                     JSON/RequestContext）
      support/
        __init__.py       适配器契约（顶层只 import 标准库 + core）
        flask_support.py   → "flask"     （含 Dash 默认底层）
        fastapi_support.py → "fastapi"
        quart_support.py   → "quart"
        uvicorn_support.py → "uvicorn"
        hypercorn_support.py → "hypercorn"

为什么这么拆（设计动机一句话）：
  · backend（HTTP 框架）与 server（ASGI/WSGI 服务器）与 app_kind
    （应用层形态，如 Dash）是**三个正交维度**，不应塞进一个 backend 参数；
  · "文件名即名字"让注册器无需 SUPPORTS 常量、无需重名检测——文件系统
    天然保证唯一；新增框架只需丢一个 *_support.py，core/registry 零改动；
  · 所有第三方依赖（含各 Web 框架）都延迟 import + 优雅降级，缺哪个都
    不影响 ``boost()`` 启动，也不影响扫描其余适配器。

Usage::

    from good_addons import boost

    boost()                                        # 纯后端增强
    boost(app, backend="flask")                   # Flask
    boost(app, backend="fastapi", server="uvicorn")  # FastAPI + Uvicorn
    boost(app, backend="flask", app_kind="dash")  # 原生 Dash（默认底层）
    boost(app, backend="fastapi", server="uvicorn", app_kind="dash")  # Dash+FastAPI

    from good_addons import trace, diag
    @trace
    def work(): ...
    print(diag())
"""

from .core import (
    AuditLog,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    EventBus,
    GracefulShutdown,
    HealthChecker,
    MetricsCollector,
    PerformanceMonitor,
    RateLimitError,
    RequestContext,
    Security,
    SystemMonitor,
    _sanitize_filter,
    cached,
    circuit_breaker,
    debounce,
    fallback,
    fast_json_dumps,
    fast_json_loads,
    get_logger,
    guarded,
    log,
    memoize,
    rate_limited,
    retry,
    set_log_level,
    timed,
    trace,
    validated,
)
from .good import boost, diag, is_dash_app

__version__ = "7.0.0"

__all__ = [
    "boost",
    "diag",
    "is_dash_app",
    "trace",
    "timed",
    "retry",
    "circuit_breaker",
    "rate_limited",
    "cached",
    "validated",
    "guarded",
    "fallback",
    "debounce",
    "memoize",
    "EventBus",
    "RequestContext",
    "CircuitBreaker",
    "CircuitState",
    "CircuitOpenError",
    "RateLimitError",
    "PerformanceMonitor",
    "SystemMonitor",
    "Security",
    "MetricsCollector",
    "AuditLog",
    "GracefulShutdown",
    "HealthChecker",
    "log",
    "get_logger",
    "set_log_level",
    "fast_json_dumps",
    "fast_json_loads",
]
