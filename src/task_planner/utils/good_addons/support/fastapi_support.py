# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.support.fastapi_support — FastAPI 增强适配器         ║
# ║  目标版本：FastAPI 0.16x / Starlette（ASGI）                       ║
# ║  License: GPLv3                                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
FastAPI 适配器 —— 为 FastAPI / Starlette 应用装配 ASGI 中间件级增强。
═══════════════════════════════════════════════════════════════════════

FastAPI 构建于 Starlette 之上，故本适配器直接操作 Starlette 原语：
    ① BaseHTTPMiddleware ── 请求上下文 + trace_id + 计时，
                            并在响应阶段注入安全头与追踪回显；
    ② 访问日志 ──────────── 按状态码 / 慢请求分级，静态路径降噪；
    ③ exception_handler ─── 兜底 Exception → JSONProblem（指纹 + request_id）；
    ④ HTTPException ─────── 保留业务抛出的状态码，仅补齐 trace 字段。

与 Flask 版的差异（ASGI 特性）：
    - 中间件是"环绕式"的，计时/日志在同一处完成后半段，无需 before/after 两个钩子；
    - 用 ``request.state`` 携带 trace_id 供异常处理读取（contextvars 在
      await 边界后可能已脱离请求作用域）；
    - WebSocket 升级请求不注入安全头（对 101 握手无意义）。

框架 import（fastapi / starlette）延迟到 enhance() 内，扫描期零副作用。
"""

from __future__ import annotations

import time
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
    """给 FastAPI / Starlette app 装配 ASGI 增强。

    Args:
        app: FastAPI 或 Starlette 实例；非 ASGI app 时安全跳过。
        **kwargs: 支持 ``csp_policy: str | None``（None → 严格 CSP）。

    Returns:
        ``{"hooks": [...], "csp": <生效CSP>}``；失败时含 ``skipped`` / ``error``。
    """
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse
    except ImportError:
        log.warning("fastapi_support: starlette 未安装，跳过 FastAPI 增强")
        return {"hooks": [], "skipped": "starlette-not-installed"}

    csp_policy: str | None = kwargs.get("csp_policy")
    effective_csp = csp_policy if csp_policy is not None else Security.CSP_STRICT
    security_headers = Security.security_headers(csp=effective_csp)

    try:
        # ── 中间件：上下文 + 计时 + 安全头 + 访问日志 ──────────────
        class _GoodAddonsMiddleware(BaseHTTPMiddleware):
            """把请求接入 good_addons 的上下文、计时与安全头管线。"""

            async def dispatch(self, request: Request, call_next: Any) -> Any:
                """处理单个请求：绑上下文 → 计时 → 注入安全头 → 记访问日志。"""
                # 入站：绑定上下文
                try:
                    tid = bind_context_from_headers(headers_from_asgi(request.headers.raw))
                except Exception as e:
                    tid = RequestContext.trace_id()
                    log.debug("fastapi context bind failed: %s", e)
                request.state.trace_id = tid
                start = time.perf_counter()
                METRICS.counter("fastapi.requests")

                try:
                    response = await call_next(request)
                except Exception as exc:  # 未被 exception_handler 覆盖的异常
                    _report_exception(exc, request, JSONResponse)
                    raise

                elapsed_ms = (time.perf_counter() - start) * 1000

                # 出站：安全头 + 追踪回显（WebSocket 握手不加）
                if response.status_code != 101:
                    for k, v in security_headers.items():
                        response.headers.setdefault(k, v)
                    response.headers[REQUEST_ID_HEADER] = tid
                    response.headers[RESPONSE_TIME_HEADER] = f"{elapsed_ms:.1f}ms"
                    response.headers[RESPONSE_TIME_MS_HEADER] = f"{elapsed_ms:.2f}"

                _log_access(request, response.status_code, elapsed_ms)
                return response

        app.add_middleware(_GoodAddonsMiddleware)

        # ── 兜底异常处理：未捕获 Exception ─────────────────────────
        @app.exception_handler(Exception)
        async def _unhandled_exc(request: Request, exc: Exception) -> JSONResponse:
            info = ExceptionFingerprinter.capture(exc)
            tid = getattr(request.state, "trace_id", "") or RequestContext.trace_id()
            log.error(
                "💥 HTTP 500 | %s: %s | request_id=%s | fingerprint=%s",
                type(exc).__name__,
                exc,
                tid,
                info.get("fingerprint"),
                exc_info=True,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "type": type(exc).__name__,
                        "message": "Internal Server Error",
                        "request_id": tid,
                        "fingerprint": info.get("fingerprint"),
                        "hint": f"Check server logs for request_id={tid}",
                    }
                },
            )

        # ── HTTPException：保留业务状态码，仅补 trace 字段 ────────
        try:
            from starlette.exceptions import HTTPException as StarletteHTTPException

            @app.exception_handler(StarletteHTTPException)
            async def _http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
                tid = getattr(request.state, "trace_id", "") or RequestContext.trace_id()
                if exc.status_code >= 500:
                    log.error(
                        "💥 HTTP %d | %s | request_id=%s",
                        exc.status_code,
                        exc.detail,
                        tid,
                    )
                elif exc.status_code >= 400:
                    log.warning(
                        "🌐 HTTP %d | %s | request_id=%s",
                        exc.status_code,
                        exc.detail,
                        tid,
                    )
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "error": {
                            "type": "HTTPException",
                            "message": exc.detail,
                            "request_id": tid,
                        }
                    },
                    headers=exc.headers,
                )

        except Exception as e:  # HTTPException handler 注册失败不致命
            log.debug("fastapi HTTPException handler 注册跳过: %s", e)

        log.info(
            "✅ FastAPI enhanced | CSP: %s | hooks: middleware(context, headers, "
            "access-log), exception-handler, http-exception-handler",
            _shorten(effective_csp),
        )
        return {
            "hooks": [
                "middleware",
                "exception-handler",
                "http-exception-handler",
            ],
            "csp": effective_csp,
        }

    except Exception as e:
        log.error("❌ FastAPI enhancement FAILED: %s", e, exc_info=True)
        return {"hooks": [], "error": str(e)}


def _log_access(request: Any, status: int, elapsed_ms: float) -> None:
    """分级访问日志：静态路径降噪，5xx/4xx/慢请求分别升级日志级别。"""
    path = request.url.path
    method = request.method
    if path.startswith(("/static/", "/assets/", "/favicon.ico")):
        if status >= 400:
            log.warning("🌐 %s %s → %d (%.0fms)", method, path, status, elapsed_ms)
        return
    if status >= 500:
        log.error("🌐 %s %s → %d (%.0fms) 🔴", method, path, status, elapsed_ms)
    elif status >= 400:
        log.warning("🌐 %s %s → %d (%.0fms) 🟡", method, path, status, elapsed_ms)
    elif elapsed_ms > 500:
        log.warning("🌐 %s %s → %d (%.0fms) 🐢", method, path, status, elapsed_ms)
    else:
        log.info("🌐 %s %s → %d (%.0fms)", method, path, status, elapsed_ms)


def _report_exception(exc: Exception, request: Any, json_response: Any) -> None:
    """中间件层兜底记录逃逸到 ASGI 边界的异常。"""
    info = ExceptionFingerprinter.capture(exc)
    tid = getattr(request.state, "trace_id", "") or RequestContext.trace_id()
    log.error(
        "💥 ASGI escape | %s: %s | request_id=%s | fingerprint=%s",
        type(exc).__name__,
        exc,
        tid,
        info.get("fingerprint"),
        exc_info=True,
    )


def _shorten(csp: str, limit: int = 60) -> str:
    return csp[:limit] + ("..." if len(csp) > limit else "")
