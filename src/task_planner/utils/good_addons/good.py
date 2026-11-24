# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.good — boost() 门面 + 应用层（Dash）正交钩子         ║
# ║  License: GPLv3                                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
good_addons.good —— 用户唯一需要认识的入口模块。
═══════════════════════════════════════════════════════════════════════

一句话：``from good_addons import boost``，然后 ``boost(app, ...)``。

┌─ 三个正交维度（这是本模块设计的全部要义）──────────────────────────┐
│                                                                    │
│   backend   ── 处理 HTTP 的 Web 框架：flask / fastapi / quart      │
│   server    ── 跑 ASGI/WSGI 的服务器：uvicorn / hypercorn         │
│   app_kind  ── 应用层形态：dash / None（默认 None）               │
│                                                                    │
│ 前两者由 registry 解析成 support/*_support.py 并调用其 enhance()； │
│ app_kind 不是 support 模块，而是本文件里的一个**应用层钩子**——    │
│ 它增强的是"框架之上的应用语义"（Dash 回调、组件路由例外、上下文    │
│ 注入），与底层到底是 Flask / FastAPI / Quart 无关。                │
│                                                                    │
│ 于是组合不再爆炸：dash-flask / dash-fastapi / dash-quart 三者的    │
│ Dash 部分共用同一份 _wrap_dash_callbacks，底层部分各自走自己的     │
│ support，两套增强正交叠加。                                        │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

典型用法::

    from good_addons import boost

    # 纯后端增强（无 Web app）
    boost()

    # Flask / 原生 Dash（Dash 默认底层即 Flask）
    boost(app, backend="flask", app_kind="dash")

    # Dash + FastAPI 后端 + Uvicorn
    boost(app, backend="fastapi", server="uvicorn", app_kind="dash")

    # 纯 FastAPI 服务
    boost(app, backend="fastapi", server="uvicorn")

当 ``app`` 被识别为 Dash 实例且调用方未显式指定 backend 时，
backend 会自动兜底为 "flask"（Dash 的默认 server），省去一行样板；
显式传了就以显式值为准。
"""

from __future__ import annotations

import os
from typing import Any

from . import core
from .core import (
    HEALTH,
    SYSTEM,
    Security,
    _setup_logging,
    log,
    set_memory_limit_mb,
)

__all__ = ["boost", "diag", "is_dash_app"]


# ╔══════════════════════════════════════════════════╗
# ║  Dash application-layer hook                    ║
# ╚══════════════════════════════════════════════════╝


def is_dash_app(app: Any) -> bool:
    """判断对象是否为 Dash 应用。

    采用三重探测，兼容 Dash 的版本演进与继承链：
      ① 类名恰为 ``Dash``；
      ② MRO 中出现名为 ``Dash`` 的基类（用户子类化场景）；
      ③ 鸭子类型：带 ``server``（有 after_request）且同时具备
         ``layout`` / ``callback_map`` 任一 Dash 特征属性。
    """
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


def _wrap_dash_callbacks(app: Any) -> int:
    """为 Dash 回调包一层错误日志，返回成功包装的回调数量。

    ⚠️ 必须同时支持同步与异步回调：

      早期实现只包同步 ``def``，对 async 回调只是把 coroutine 对象原样
      返回给 Dash——真正的 ``await`` 发生在 wrapper 之外，异常永远进不了
      本 wrapper。结果恰恰是最需要日志的 async 回调反而没有日志。

      修复：按 ``iscoroutinefunction`` 分派——async 函数用 async wrapper
      并 ``await orig(...)``；sync 函数保持原逻辑。

    每个 callback 的异常：做指纹 → 记结构化日志 → **重新抛出**，让 Dash
    自身的错误处理（如 prevent_initial_call / 前端报错）依旧生效。
    """
    import functools
    import inspect

    callback_map = getattr(app, "callback_map", None)
    if not callback_map:
        return 0

    wrapped = 0
    for _output_key, cb_info in callback_map.items():
        original_func = cb_info.get("callback")
        if original_func is None:
            continue

        if inspect.iscoroutinefunction(original_func):

            async def _async_wrapper(*args: Any, _orig: Any = original_func, **kwargs: Any) -> Any:
                try:
                    return await _orig(*args, **kwargs)
                except Exception as exc:
                    _log_dash_callback_error(exc, async_cb=True)
                    raise

            cb_info["callback"] = functools.wraps(original_func)(_async_wrapper)
        else:

            def _sync_wrapper(*args: Any, _orig: Any = original_func, **kwargs: Any) -> Any:
                try:
                    return _orig(*args, **kwargs)
                except Exception as exc:
                    _log_dash_callback_error(exc, async_cb=False)
                    raise

            cb_info["callback"] = functools.wraps(original_func)(_sync_wrapper)

        wrapped += 1

    return wrapped


def _log_dash_callback_error(exc: Exception, *, async_cb: bool) -> None:
    """Dash 回调异常的统一记录：指纹 + 结构化日志（不吞，交由上层重抛）。"""
    from .core import ExceptionFingerprinter, RequestContext

    info = ExceptionFingerprinter.capture(exc)
    tid = RequestContext.trace_id()
    kind = "async callback" if async_cb else "callback"
    log.error(
        "💥 Dash %s error | %s: %s | request_id=%s | fingerprint=%s",
        kind,
        type(exc).__name__,
        exc,
        tid,
        info.get("fingerprint"),
        exc_info=True,
    )


def _apply_dash_hook(app: Any) -> dict[str, Any]:
    """应用 Dash 专属增强，返回报告片段。

    包含：
      - 回调错误日志包装（_wrap_dash_callbacks）；
      - CSP 例外提示：Dash 需要 unsafe-inline/eval 与 ws 连接，
        若底层 support 选用的是严格 CSP，这里在报告里显式提示用户
        改用 CSP_DASH_COMPAT，避免"装了却白屏"的静默失败。

    注意：本函数只做应用层逻辑，安全头/请求日志/异常处理等由 backend
    support（flask/fastapi/quart）负责，两者不重叠。
    """
    report: dict[str, Any] = {"app_kind": "dash"}
    try:
        wrapped = _wrap_dash_callbacks(app)
        report["callbacks_wrapped"] = wrapped
        if wrapped:
            log.info("🔗 Wrapped %d Dash callbacks with error logging", wrapped)
    except Exception as e:
        log.warning("⚠️ Dash callback wrapping failed (non-fatal): %s", e, exc_info=True)
        report["callbacks_wrapped"] = 0
        report["callback_wrap_error"] = str(e)

    report["csp_hint"] = (
        "Dash 需要 script-src 'unsafe-eval' 'unsafe-inline' 与 ws/wss connect-src；"
        "若页面白屏，请把 csp_policy 设为 Security.CSP_DASH_COMPAT 或 None"
        "（Dash 场景 None 会自动选用兼容策略）"
    )
    return report


# ╔══════════════════════════════════════════════════╗
# ║  Bootstrap                                       ║
# ╚══════════════════════════════════════════════════╝

_boost_initialized = False


def boost(
    app: Any = None,
    *,
    backend: str | None = None,
    server: str | None = None,
    app_kind: str | None = None,
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
    """引导（bootstrap）整个运行时增强层——用户唯一需要调用的入口。

    Args:
        app: 要增强的对象。可为 Web 框架 app（Flask/FastAPI/Quart）、
            Dash app、或服务器对象（uvicorn.Config/Server、hypercorn.Config）。
            为 None 时只做进程级通用增强（日志/看门狗/退出/异常钩子）。
        backend: 处理 HTTP 的 Web 框架名，``flask`` / ``fastapi`` / ``quart``。
            缺省时若 ``app`` 被识别为 Dash，自动兜底为 ``"flask"``
            （Dash 默认底层）。
        server: ASGI/WSGI 服务器名，``uvicorn`` / ``hypercorn``。
        app_kind: 应用层形态，目前支持 ``"dash"``；其余为 None。
            触发本模块内的 Dash 专属钩子，与 backend 正交叠加。
        log_level: 日志级别（"DEBUG"/"INFO"/...）。
        memory_watchdog: 是否启动内存看门狗线程。
        memory_limit_mb: 内存阈值（MB），看门狗与系统健康探针共用，
            探针按此值的 90% 判 degraded。
        graceful_shutdown: 是否安装 SIGTERM/SIGINT 优雅退出。
        rich_logging: 是否优先使用 Rich 美化日志（非 JSON 模式下）。
        sanitize_logs: 是否对日志做敏感信息脱敏（当前恒启用，保留参数
            以便未来开关；False 时仅记录一条告警）。
        exception_fingerprint: 是否安装全局未捕获异常钩子。
        csp_policy: 覆盖默认 CSP。None 时：Dash 场景自动选
            ``Security.CSP_DASH_COMPAT``，否则 ``Security.CSP_STRICT``。
        json_log: 是否输出结构化 JSON 日志；None 时读环境变量
            ``GOOD_ADDONS_LOG_JSON``。

    Returns:
        报告 dict，含 ``log`` / ``backends`` / ``app_kind_report`` /
        ``components`` 等，便于调用方自检本次装了什么。

    Raises:
        ValueError: backend / server 名字无法在 support/ 中解析时。
    """
    global _boost_initialized

    # 内存阈值先落盘，供系统健康探针即时读取（P1-1：单一来源）
    set_memory_limit_mb(memory_limit_mb)

    # ── 日志：json_log 参数 > 环境变量 > 当前值；_setup_logging 幂等 ──
    if json_log is not None:
        core._LOG_JSON_FORMAT = json_log
    elif os.getenv("GOOD_ADDONS_LOG_JSON", "false").lower() == "true":
        core._LOG_JSON_FORMAT = True
    if not sanitize_logs:
        pass  # 脱敏为安全底线，暂不提供关闭；保留参数兼容旧签名

    log = _setup_logging(
        level=log_level,
        rich=rich_logging,
        json_format=core._LOG_JSON_FORMAT,
    )

    if _boost_initialized:
        log.debug("boost() already initialized, skipping")
        return {"_skipped": True, "log": log, "perf": core.PERF, "events": core.EVENTS}

    log.info("🚀 good_addons v%s bootstrapping...", core.__version__)

    # ── 进程级通用增强 ────────────────────────────────────────
    if graceful_shutdown:
        core.SHUTDOWN.install()
    if memory_watchdog:
        core.SYSTEM.start_memory_watchdog(max_mb=memory_limit_mb)
    if exception_fingerprint:
        core._install_exception_hook()

    report: dict[str, Any] = {
        "_skipped": False,
        "log": log,
        "perf": core.PERF,
        "events": core.EVENTS,
        "audit": core.AUDIT,
        "shutdown": core.SHUTDOWN,
        "system": core.SYSTEM,
        "health": core.HEALTH,
        "security": Security,
        "backends": [],
    }

    # ── 应用层（Dash）自动识别与 backend 兜底 ─────────────────
    if app is not None and app_kind is None and is_dash_app(app):
        app_kind = "dash"
        log.info("🔎 Detected Dash app → app_kind='dash'")

    if app is not None and app_kind == "dash" and backend is None:
        backend = "flask"  # Dash 默认底层
        log.info("🔎 Dash app 未显式指定 backend → 兜底 backend='flask'")

    # ── 应用层正交钩子（Dash）──────────────────────────────────
    if app is not None and app_kind == "dash":
        # Dash 场景默认用兼容 CSP，除非调用方显式覆盖
        effective_csp = csp_policy if csp_policy is not None else Security.CSP_DASH_COMPAT
        report["app_kind_report"] = _apply_dash_hook(app)
        report["csp"] = effective_csp
    else:
        effective_csp = csp_policy  # None → 各 support 自己用严格 CSP

    # ── backend / server 走 registry 解析并逐个 enhance ───────
    if app is not None and (backend or server):
        from .registry import resolve

        for adapter in resolve(backend, server):
            try:
                result = adapter.module.enhance(app, csp_policy=effective_csp, app_kind=app_kind)
                report["backends"].append({"name": adapter.name, **result})
            except ValueError:
                raise  # 名字解析失败是用户错误，直接抛出
            except Exception as e:  # 单个适配器失败不拖垮整体
                log.error("❌ support %r enhance 失败: %s", adapter.name, e, exc_info=True)
                report["backends"].append({"name": adapter.name, "error": str(e)})

    _boost_initialized = True

    deps = [
        n
        for n, f in [
            ("rich", core._HAS_RICH),
            ("psutil", core._HAS_PSUTIL),
            ("orjson", core._HAS_ORJSON),
            ("pydantic", core._HAS_PYDANTIC),
            ("prometheus", core._HAS_PROMETHEUS),
            ("crypto", core._HAS_CRYPTO),
        ]
        if f
    ]
    log.info(
        "boost() complete | deps: %s | pid: %d | json_log: %s | backend: %s | "
        "server: %s | app_kind: %s",
        ", ".join(deps) or "stdlib",
        os.getpid(),
        core._LOG_JSON_FORMAT,
        backend,
        server,
        app_kind,
    )

    return report


# ╔══════════════════════════════════════════════════╗
# ║  Diagnostics                                     ║
# ╚══════════════════════════════════════════════════╝


def diag(*, perf_top: int = 15, audit_last: int = 20, include_health: bool = True) -> str:
    """一键诊断：汇总性能 / 审计 / 系统 / 指标 / 事件 / 健康状态为可读文本。

    Args:
        perf_top: 性能表展示的函数条目上限。
        audit_last: 最近审计记录条数。
        include_health: 是否附健康报告。

    Returns:
        多行文本；同时会以一条 INFO 日志整体输出，便于远程采集。
    """
    from datetime import datetime as _dt

    sections: list[str] = []

    sections.append("═══ Performance ═══")
    sections.append(core.PERF.format_table(top_n=perf_top))

    sections.append("\n═══ Recent Audit ═══")
    entries = core.AUDIT.query(last_n=audit_last)
    if entries:
        for e in entries:
            ts = e["timestamp"][:19]
            sections.append(
                f"  {ts} | {e['action']:<20} | {e['actor']:<15} | {str(e.get('details', ''))[:80]}"
            )
    else:
        sections.append("  (empty)")

    sections.append("\n═══ System ═══")
    mem = SYSTEM.memory_info()
    cpu = SYSTEM.cpu_percent()
    if mem:
        sections.append(
            f"  RSS: {mem['rss_mb']:.1f} MB | VMS: {mem['vms_mb']:.1f} MB | CPU: {cpu:.1f}%"
        )
    else:
        sections.append("  (psutil not available)")

    sections.append("\n═══ Metrics ═══")
    metrics = core.METRICS.snapshot()
    if metrics:
        for k, v in sorted(metrics.items()):
            sections.append(f"  {k}: {v:.0f}")
    else:
        sections.append("  (empty)")

    sections.append("\n═══ Recent Events ═══")
    recent = core.EVENTS.get_history(last_n=10)
    if recent:
        for e in recent:
            ts = _dt.fromtimestamp(e["time"], tz=core.UTC).strftime("%H:%M:%S")
            sections.append(f"  {ts} | {e['event']:<25} | seq={e['seq']}")
    else:
        sections.append("  (empty)")

    if include_health:
        sections.append("\n" + HEALTH.format())

    output = "\n".join(sections)
    log.info("📊 Diagnostic dump:\n%s", output)
    return output
