# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.support.quart_support — Quart 增强适配器             ║
# ║  目标版本：Quart 0.23.x（Pallets 官方，Flask API 的 ASGI 实现）    ║
# ║  License: GPLv3                                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
Quart 适配器 —— 为 Quart 应用装配请求级增强。
═══════════════════════════════════════════════════════════════════════

Quart 复用 Flask 的装饰器风格（before_request / after_request /
errorhandler），但请求处理函数是 **协程**，故本适配器与 flask_support
结构对称、差异只在 ``async def`` + ``await``：
    ① before_request ── async，绑定上下文 + trace_id；
    ② after_request ── async，注入安全头 + 追踪回显；
    ③ after_request ── async，分级访问日志；
    ④ errorhandler ── async，异常指纹 + JSON 错误体。

与 Flask 版共用 core 的 Header 约定与 Security 预设，行为一致——
这也是把公共层收敛到 core 的收益：换框架不换语义。

框架 import（quart）延迟到 enhance() 内，扫描期零副作用。
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
    headers_from_asgi,
    log,
)


def enhance(app: Any, **kwargs: Any) -> dict[str, Any]:
    """给 Quart app 装配请求级增强。

    Args:
        app: Quart 实例；非 Quart app 时安全跳过。
        **kwargs: 支持 ``csp_policy: str | None``（None → 严格 CSP）。

    Returns:
        ``{"hooks": [...], "csp": <生效CSP>}``；失败时含 ``skipped`` / ``error``。
    """
    try:
        from quart import Quart, request
    except ImportError:
        log.warning("quart_support: quart 未安装，跳过 Quart 增强")
        return {"hooks": [], "skipped": "quart-not-installed"}

    if not isinstance(app, Quart):
        return {"hooks": [], "skipped": f"not-a-quart-app: {type(app).__name__}"}

    csp_policy: str | None = kwargs.get("csp_policy")
    effective_csp = csp_policy if csp_policy is not None else Security.CSP_STRICT
    security_headers = Security.security_headers(csp=effective_csp)

    try:
        # ── Hook 1: 请求上下文 + trace_id ────────────────────────
        @app.before_request
        async def _bind_context() -> None:
            try:
                bind_context_from_headers(headers_from_asgi(request.headers))
                METRICS.counter("quart.requests")
            except Exception as e:
                log.debug("quart before_request context bind failed: %s", e)

        # ── Hook 2: 安全响应头 + 追踪回显 ────────────────────────
        @app.after_request
        async def _add_security_headers(response: Any) -> Any:
            try:
                for k, v in security_headers.items():
                    response.headers[k] = v
                response.headers[REQUEST_ID_HEADER] = RequestContext.trace_id()
                response.headers[RESPONSE_TIME_HEADER] = f"{RequestContext.elapsed() * 1000:.1f}ms"
                response.headers[RESPONSE_TIME_MS_HEADER] = f"{RequestContext.elapsed() * 1000:.2f}"
            except Exception as e:
                log.debug("quart security headers failed: %s", e)
            return response

        # ── Hook 3: 结构化访问日志 ───────────────────────────────
        @app.after_request
        async def _log_request(response: Any) -> Any:
            try:
                ms = RequestContext.elapsed() * 1000
                path = request.path
                status = response.status_code
                if path.startswith(("/static/", "/assets/", "/favicon.ico")):
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
                log.debug("quart access log failed: %s", e)
            return response

        # ── Hook 4: 兜底异常处理 ────────────────────────────────
        @app.errorhandler(Exception)
        async def _handle_error(exc: Exception) -> Any:
            from quart import jsonify

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
            "✅ Quart enhanced | CSP: %s | hooks: context, security-headers, "
            "access-log, error-handler",
            _shorten(effective_csp),
        )
        return {
            "hooks": ["context", "security-headers", "access-log", "error-handler"],
            "csp": effective_csp,
        }

    except Exception as e:
        log.error("❌ Quart enhancement FAILED: %s", e, exc_info=True)
        return {"hooks": [], "error": str(e)}


def _shorten(csp: str, limit: int = 60) -> str:
    return csp[:limit] + ("..." if len(csp) > limit else "")
