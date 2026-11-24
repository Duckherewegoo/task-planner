# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.support.hypercorn_support — Hypercorn 增强适配器     ║
# ║  目标版本：Hypercorn 0.18.x（ASGI/WSGI 双模服务器）               ║
# ║  License: GPLv3                                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
Hypercorn 适配器 —— 把 Hypercorn 服务器接入 good_addons 的优雅退出与
日志体系。
═══════════════════════════════════════════════════════════════════════

与 uvicorn_support 对称，但 Hypercorn 有两点独有特性需要在 docstring
里讲清，避免使用者踩坑：

  · **ASGI 与 WSGI 双模**：Hypercorn 既能跑 ASGI（FastAPI/Quart）也能
    跑 WSGI（Flask）。本适配器对两种模式都适用，因为增强点都在
    *服务器进程*层面（信号、日志、退出），与 app 协议无关。

  · **多 worker 语义**：Hypercorn 的 ``Config.workers > 1`` 时会 fork
    多个进程，优雅退出钩子会在**每个 worker** 里各触发一次。本适配器
    的退出报告因此只做只读快照打印，不做需要全局唯一性的动作。

增强点：
    ① 优雅退出 ── 注册 SIGTERM/SIGINT → GracefulShutdown；
    ② 日志对齐 ── JSON 日志模式下把 access log 交给结构化请求日志。

框架 import（hypercorn）延迟到 enhance() 内，扫描期零副作用。
"""

from __future__ import annotations

from typing import Any

from good_addons.core import (
    _LOG_JSON_FORMAT,
    HEALTH,
    SHUTDOWN,
    SYSTEM,
    log,
)


def enhance(app: Any, **kwargs: Any) -> dict[str, Any]:
    """增强 Hypercorn（app / Config 皆可）。

    Args:
        app: ASGI/WSGI app 或 ``hypercorn.config.Config`` 实例。
        **kwargs:
            - ``access_log: bool`` 是否输出访问日志
              （默认跟随 JSON 日志开关，同 uvicorn）；
            - ``register_shutdown: bool`` 是否接入优雅退出（默认 True）。

    Returns:
        ``{"hooks": [...]}``。
    """
    hooks: list[str] = []

    if kwargs.get("register_shutdown", True):
        try:
            SHUTDOWN.register(_graceful_report, priority=10)
            SHUTDOWN.install()
            hooks.append("graceful-shutdown")
        except Exception as e:
            log.debug("hypercorn graceful-shutdown 接入跳过: %s", e)

    config = _extract_config(app)
    if config is not None:
        try:
            access_log = kwargs.get("access_log", not _LOG_JSON_FORMAT)
            config.accesslog = "-" if access_log else None
            hooks.append("log-align")
        except Exception as e:
            log.debug("hypercorn config 日志对齐失败: %s", e)
    else:
        log.debug(
            "hypercorn: 传入的是 app 而非 Config；进程级增强（优雅退出）"
            "已通过信号处理器生效，日志对齐请在构造 Config 后再次调用"
        )

    log.info("✅ Hypercorn enhanced | hooks: %s", ", ".join(hooks) or "(none-applied)")
    return {"hooks": hooks}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Internals
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _graceful_report() -> None:
    """退出钩子：只读快照打印。多 worker 下每个进程各触发一次，故保持轻量。"""
    try:
        log.info("🛑 [hypercorn] shutdown: %s", HEALTH.format(timeout=2.0).replace("\n", " | "))
    except Exception:
        pass
    try:
        mem = SYSTEM.memory_info()
        if mem:
            log.info("🛑 [hypercorn] final RSS: %.1fMB", mem.get("rss_mb", 0))
    except Exception:
        pass


def _extract_config(app: Any) -> Any:
    """从入参取出 hypercorn.Config；不是 Config 则返回 None。"""
    try:
        from hypercorn.config import Config
    except ImportError:
        return None
    if isinstance(app, Config):
        return app
    return None
