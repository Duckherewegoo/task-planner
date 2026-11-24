# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.support.flask_support — Flask 增强适配器             ║
# ║  目标版本：Flask 3.1.x（Python ≥ 3.9）                            ║
# ║  License: GPLv3                                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
Flask 适配器 —— 为 Flask（及以 Flask 为底层的应用，如 Dash 默认 server）
装配请求级增强。
═══════════════════════════════════════════════════════════════════════

装了什么（全部通过 Flask 信号钩子，零侵入业务代码）：
    ① after_request ── 注入安全响应头 + X-Request-ID + X-Response-Time；
    ② before_request── 从 X-Trace-ID / X-Request-ID / X-Correlation-ID
                       提取（或新生成）trace_id，并打点请求起始时间；
    ③ after_request ── 结构化请求日志（按状态码 / 慢请求分级），
                       对静态与 component-suites 路径降噪；
    ④ errorhandler  ── 兜底异常处理：异常指纹 + 结构化 JSON 错误响应。

Dash 说明：本适配器只认"Flask WSGI 层"。若传入的是 Dash app，会经
``app.server`` 取到底层 Flask 打同样的钩子；Dash 回调层的错误包装
属于应用层逻辑，由 ``good.py`` 的 ``app_kind="dash"`` 钩子负责，与本
适配器正交——因此这里**不**做 ``_is_dash_app`` 判定与回调包装。

框架 import（flask）延迟到 enhance() 内，保证 registry 扫描零副作用。
"""

from __future__ import annotations

from typing import Any

from good_addons.core import (
    METRICS,
    REQUEST_ID_HEADER,
    RESPONSE_TIME_HEADER,
    RESPONSE_TIME_MS_HEADER,
    ExceptionFingerprinter,
    RequestContext,
    Security,
    bind_context_from_headers,
    log,
)
from good_addons.core import (
    raw_headers_from_environ as _raw_headers,
)


def enhance(app: Any, **kwargs: Any) -> dict[str, Any]:
    """给 Flask app 装配请求级增强。

    Args:
        app: Flask 实例，或带 ``.server``（Flask）属性的应用对象
            （典型如 Dash）。非 Flask 体系时安全跳过。
        **kwargs: 支持 ``csp_policy: str | None`` — 覆盖默认 CSP；
            None 时用严格 CSP（Dash 兼容 CSP 由 good.py 的 app_kind
            钩子在需要时另行注入）。

    Returns:
        ``{"hooks": [...], "csp": <生效CSP>}``；无法识别 app 时
        ``{"hooks": [], "skipped": <原因>}``。
    """
    # ── 延迟 import：仅在实际增强时导入 Flask，扫描期零副作用 ──
    try:
        from flask import Flask, request
    except ImportError:
        log.warning("flask_support: flask 未安装，跳过 Flask 增强")
        return {"hooks": [], "skipped": "flask-not-installed"}

    # 取出底层 Flask：Dash / 其它包装器把 WSGI app 挂在 .server 上
    server = getattr(app, "server", app)
    if not isinstance(server, Flask):
        return {"hooks": [], "skipped": f"not-a-flask-app: {type(server).__name__}"}

    csp_policy: str | None = kwargs.get("csp_policy")
    effective_csp = csp_policy if csp_policy is not None else Security.CSP_STRICT

    try:
        # ── Hook 1: 请求上下文 + trace_id ────────────────────────
        @server.before_request
        def _bind_context() -> None:
            try:
                # 分布式追踪 + 身份：统一走 core 的 Header 约定。
                # 必须传 request.environ 而非 dict(request.headers)：
                # Werkzeug 会把头名标题化（X-Trace-ID → X-Trace-Id），
                # 而 core 要求按规范写法精确匹配，所以用未改写的
                # HTTP_* 原始头（environ 里原样保留客户端发来的大小写）。
                bind_context_from_headers(_raw_headers(request.environ))
                METRICS.counter("flask.requests")
            except Exception as e:  # 上下文绑定失败不应阻断请求
                log.debug("flask before_request context bind failed: %s", e)

        # ── Hook 2: 安全响应头 + 追踪回显 ────────────────────────
        @server.after_request
        def _add_security_headers(response: Any) -> Any:
            try:
                for k, v in Security.security_headers(csp=effective_csp).items():
                    response.headers[k] = v
                response.headers[REQUEST_ID_HEADER] = RequestContext.trace_id()
                response.headers[RESPONSE_TIME_HEADER] = f"{RequestContext.elapsed() * 1000:.1f}ms"
                response.headers[RESPONSE_TIME_MS_HEADER] = f"{RequestContext.elapsed() * 1000:.2f}"
            except Exception as e:
                log.debug("flask security headers failed: %s", e)
            return response

        # ── Hook 3: 结构化访问日志 ───────────────────────────────
        @server.after_request
        def _log_request(response: Any) -> Any:
            try:
                ms = RequestContext.elapsed() * 1000
                path = request.path
                status = response.status_code
                # 静态资源 / Dash 组件包：仅在出错时记录，避免刷屏
                if path.startswith(("/_dash-component-suites/", "/assets/", "/static/")):
                    if status >= 400:
                        log.warning("🌐 %s %s → %d (%.0fms)", request.method, path, status, ms)
                    return response
                if status >= 500:
                    log.error("🌐 %s %s → %d (%.0fms) 🔴", request.method, path, status, ms)
                elif status >= 400:
                    log.warning("🌐 %s %s → %d (%.0fms) 🟡", request.method, path, status, ms)
                elif ms > 500:
                    log.warning("🌐 %s %s → %d (%.0fms) 🐢", request.method, path, status, ms)
                else:
                    log.info("🌐 %s %s → %d (%.0fms)", request.method, path, status, ms)
            except Exception as e:
                log.debug("flask access log failed: %s", e)
            return response

        # ── Hook 4: 兜底异常处理 → 指纹 + JSON 错误体 ───────────
        @server.errorhandler(Exception)
        def _handle_error(exc: Exception) -> Any:
            status = getattr(exc, "code", 500)
            if not isinstance(status, int) or status < 400:
                status = 500

            info = ExceptionFingerprinter.capture(exc)
            tid = RequestContext.trace_id()
            log.error(
                "💥 HTTP %d | %s: %s | request_id=%s | fingerprint=%s",
                status,
                type(exc).__name__,
                exc,
                tid,
                info.get("fingerprint"),
                exc_info=True,
            )

            from flask import jsonify

            return (
                jsonify(
                    {
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc) if status < 500 else "Internal Server Error",
                            "request_id": tid,
                            "fingerprint": info.get("fingerprint"),
                            "hint": f"Check server logs for request_id={tid}",
                        }
                    }
                ),
                status,
            )

        log.info(
            "✅ Flask enhanced | CSP: %s | hooks: context, security-headers, "
            "access-log, error-handler",
            _shorten(effective_csp),
        )
        return {
            "hooks": ["context", "security-headers", "access-log", "error-handler"],
            "csp": effective_csp,
        }

    except Exception as e:
        log.error("❌ Flask enhancement FAILED: %s", e, exc_info=True)
        return {"hooks": [], "error": str(e)}


def _shorten(csp: str, limit: int = 60) -> str:
    """CSP 串过长时截断，仅用于日志展示。"""
    return csp[:limit] + ("..." if len(csp) > limit else "")
