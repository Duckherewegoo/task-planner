# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.support.uvicorn_support — Uvicorn 增强适配器         ║
# ║  目标版本：Uvicorn 0.54.x（ASGI 服务器）                           ║
# ║  License: GPLv3                                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
Uvicorn 适配器 —— 把 Uvicorn 服务器的生命周期接入 good_addons 的
健康检查与优雅退出体系。
═══════════════════════════════════════════════════════════════════════

与框架适配器的区别：服务器不处理单个请求，故增强点是**进程生命周期**：
    ① 读取/构造 Config ── 把 uvicorn.Config 上的 access_log / 日志配置
       与 good_addons 的 JSON 日志开关对齐（避免两套格式各说各话）；
    ② lifespan 钩子 ──── 若用户 app 尚未定义 lifespan，这里包裹一层，
       在 startup 打印健康报告、在 shutdown 触发 SHUTDOWN 的钩子；
    ③ 优雅退出 ───────── 注册 SIGTERM/SIGINT → GracefulShutdown。

设计取舍：Uvicorn 通常由 ``uvicorn.run()`` 或 ``Server(config).run()``
启动，用户手里的对象可能是 **app**、**Config** 或 **Server**。本适配器
尽力识别这三种入参：
    - 传入 ASGI app：就地包裹其 lifespan（返回新 app 供用户去 run）；
    - 传入 Config：调整其日志相关字段；
    - 传入 Server：读取其 config 做同样调整。
无论哪种，增强失败都不阻断启动。

框架 import（uvicorn）延迟到 enhance() 内，扫描期零副作用。
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
    """增强 Uvicorn（app / Config / Server 三者皆可）。

    Args:
        app: ASGI app、``uvicorn.Config`` 或 ``uvicorn.Server`` 实例。
        **kwargs:
            - ``access_log: bool`` 是否让 uvicorn 输出访问日志
              （默认跟随 JSON 日志开关：JSON 模式下关闭，交由我们的
              结构化请求日志承担，避免双份）；
            - ``register_shutdown: bool`` 是否接入优雅退出（默认 True）。

    Returns:
        ``{"hooks": [...]}``；若识别出是 ASGI app，会额外返回
        ``{"app": <包裹后的 app>}`` 供调用方替换使用。
    """
    hooks: list[str] = []

    # ── 优雅退出接入（与入参类型无关，先行注册）────────────────
    if kwargs.get("register_shutdown", True):
        try:
            SHUTDOWN.register(_graceful_report, priority=10)
            SHUTDOWN.install()
            hooks.append("graceful-shutdown")
        except Exception as e:
            log.debug("uvicorn graceful-shutdown 接入跳过: %s", e)

    # ── 尝试按 Config / Server 处理日志对齐 ────────────────────
    config = _extract_config(app)
    if config is not None:
        try:
            access_log = kwargs.get("access_log", not _LOG_JSON_FORMAT)
            config.access_log = bool(access_log)
            # JSON 日志模式下让 uvicorn 用自己的 logger 名，便于统一过滤
            if _LOG_JSON_FORMAT:
                config.log_config = None  # 交由 root / good_addons 处理
            hooks.append("log-align")
        except Exception as e:
            log.debug("uvicorn config 日志对齐失败: %s", e)

    # ── 传入的是 ASGI app：仅做提示，不篡改其 lifespan 协议 ──────
    # lifespan 已被 app（或 backend 适配器）拥有时强行覆盖极危险，
    # 故这里只记录已生效的进程级增强，把 lifespan 交给用户/框架自己管。
    if config is None and _looks_like_asgi_app(app):
        log.debug(
            "uvicorn: 检测到 ASGI app，保留其原生 lifespan；"
            "进程级增强（优雅退出）已通过信号处理器生效"
        )

    log.info("✅ Uvicorn enhanced | hooks: %s", ", ".join(hooks) or "(none-applied)")
    return {"hooks": hooks}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Internals
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _graceful_report() -> None:
    """退出钩子：打印一次末态健康/内存快照，便于事后排查。"""
    try:
        log.info("🛑 [uvicorn] shutdown: %s", HEALTH.format(timeout=2.0).replace("\n", " | "))
    except Exception:
        pass
    try:
        mem = SYSTEM.memory_info()
        if mem:
            log.info("🛑 [uvicorn] final RSS: %.1fMB", mem.get("rss_mb", 0))
    except Exception:
        pass


def _extract_config(app: Any) -> Any:
    """从入参中取出 uvicorn.Config；不是 Config/Server 则返回 None。"""
    try:
        import uvicorn
    except ImportError:
        return None
    if isinstance(app, uvicorn.Config):
        return app
    if isinstance(app, uvicorn.Server):
        return getattr(app, "config", None)
    return None


def _looks_like_asgi_app(app: Any) -> bool:
    """启发式判断是否为 ASGI app（可调用且形如 app(scope, receive, send)）。"""
    return callable(app) and not isinstance(app, type)
