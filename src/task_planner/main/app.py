"""app.py — Dash app 实例 + main()，FastAPI backend 版。

架构约定（详见 docs/adr/0009-fastapi-async-boundary.md）：
  - Dash 使用 FastAPI backend，回调直接跑在 uvicorn 的持久 event loop 上，
    asyncio.create_task 创建的任务天然跨回调存活，无需独立后台 loop。
  - 启动方式为 uvicorn，不再使用 app.run() / Werkzeug。
  - 生命周期钩子挂在 app.server.router 上（见下方「生命周期钩子」注释）。

本模块的三条硬约束：
  1. boost() 必须在 register_config_callbacks() 之前调用——Dash 的回调包装
     是遍历已注册回调实现的，后注册的回调不会被增强覆盖。
  2. 生命周期钩子注册必须幂等——uvicorn --reload 会让子进程重新导入本模块。
  3. shutdown 逐项独立兜底——任何一项清理失败都不能阻断后续清理。
"""

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

# dash / dash_cytoscape / uvicorn 均无官方类型桩，需显式压制 import-untyped；
# 压制后仍需处理 reportUnknownMemberType，故对 load_extra_layouts 单独再压一次。
import dash  # type: ignore[import-untyped]
import dash_cytoscape as cyto  # type: ignore[import-untyped]
import uvicorn  # type: ignore[import-untyped]

from task_planner.config_ui.callbacks import register_config_callbacks
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import VALID_PORT_RANGE
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.utils.good_addons.good import boost

logger = get_logger("task_planner.ui")

cyto.load_extra_layouts()  # type: ignore[reportUnknownMemberType]

# app 显式标注 Any：dash 无类型 stub，标注后避免 reportUnknownMemberType 级联
app: Any = dash.Dash(
    __name__,
    backend="fastapi",
    title="🧠 通用任务规划助手",
    assets_folder="./assets",
    suppress_callback_exceptions=True,
)

# ── 增强：必须在 register_config_callbacks 之前 ────────────────────────
# backend="fastapi" 必须显式给出：boost 见到 Dash 对象会默认按 flask 兜底，
# 与实际运行的 FastAPI backend 不一致，中间件会挂错层。
# app_kind="dash" 触发 Dash 专属回调包装（错误日志 + trace_id 注入），
# 该逻辑与底层框架正交，不会与 fastapi_support 的中间件重复。
_boost_report: dict[str, Any] = boost(
    app,
    backend="fastapi",
    app_kind="dash",
    log_level="DEBUG" if _hub.dev.DEBUG else "INFO",
    memory_watchdog=True,
    memory_limit_mb=2048,
)

os.environ["DASH_DISABLE_VERSION_CHECK"] = str(_hub.dev.DASH_DISABLE_VERSION_CHECK)

register_config_callbacks(app, with_drawer=True)

# 过期会话清理的轮询间隔（秒）。
# 注意：这是「多久扫一次」，与 TaskStreamState._ttl（「一条状态活多久」）
_SESSION_CLEANUP_INTERVAL: int = _hub.dev.SESSION_CLEANUP_INTERVAL

# 单个 shutdown 步骤的兜底超时（秒）
_SHUTDOWN_STEP_TIMEOUT: float = 5.0

_cleanup_task: asyncio.Task[None] | None = None
_hooks_registered: bool = False


async def _cleanup_loop() -> None:
    """周期性清理过期会话，直到被取消。

    常驻任务跑在 uvicorn 的持久 loop 上；捕获 CancelledError 后正常退出，
    其余异常只记录不中断——单次清理失败不应让整个循环停摆。
    """
    from task_planner.services.agent.session import cleanup_stale_sessions

    while True:
        try:
            await asyncio.sleep(_SESSION_CLEANUP_INTERVAL)
            cleaned = await cleanup_stale_sessions()
            if cleaned:
                logger.info(
                    "[Lifespan] 清理了 %d 个过期会话 | Cleaned %d stale sessions",
                    cleaned,
                    cleaned,
                )
        except asyncio.CancelledError:
            logger.info("[Lifespan] 清理循环已取消，退出 | Cleanup loop cancelled, exiting.")
            return
        except Exception as exc:
            logger.error(
                "[Lifespan] 清理循环出错（继续运行） | Cleanup loop error, continuing: %s", exc
            )


async def _on_startup() -> None:
    """启动钩子：拉起过期会话清理循环。"""
    global _cleanup_task

    if _cleanup_task is not None and not _cleanup_task.done():
        return  # --reload 导致重复触发时，不重复拉起

    _cleanup_task = asyncio.create_task(_cleanup_loop())
    logger.info("[Lifespan] 会话清理任务已启动 | Session cleanup task started")


async def _stop_cleanup_task() -> None:
    """取消清理循环并等待其退出，超时则告警。"""
    global _cleanup_task

    task = _cleanup_task
    if task is None:
        return
    _cleanup_task = None

    task.cancel()
    try:
        # shield 让超时与取消可区分：超时说明任务没退干净
        await asyncio.wait_for(asyncio.shield(task), timeout=_SHUTDOWN_STEP_TIMEOUT)
    except TimeoutError:
        logger.warning(
            "[Lifespan] 清理循环未能在 %.1f 秒内停止 | Cleanup loop did not stop within %.1fs",
            _SHUTDOWN_STEP_TIMEOUT,
            _SHUTDOWN_STEP_TIMEOUT,
        )
    except asyncio.CancelledError:
        pass  # 预期路径：cancel() 的结果


async def _shutdown_quietly(
    name: str,
    action: Callable[[], Awaitable[Any]],
) -> None:
    """执行单个 shutdown 步骤，失败只记录不抛出。

    关闭阶段必须「尽力而为」：DB 关闭失败不该阻断 LLM 客户端关闭，
    否则连接池会泄漏。每步独立兜底，保证后续步骤一定被执行。
    """
    try:
        await asyncio.wait_for(action(), timeout=_SHUTDOWN_STEP_TIMEOUT)
        logger.info("[Lifespan] %s 已关闭 | %s closed", name, name)
    except TimeoutError:
        logger.warning(
            "[Lifespan] %s 关闭超时（%.1fs） | %s shutdown timed out",
            name,
            _SHUTDOWN_STEP_TIMEOUT,
            name,
        )
    except Exception as exc:
        logger.error("[Lifespan] %s 关闭失败 | %s shutdown failed: %s", name, name, exc)


async def _on_shutdown() -> None:
    """关闭钩子：按依赖倒序清理，逐项独立兜底。

    顺序有讲究：先停清理循环（它还会碰 DB）→ 再停 UI 状态清理器
    → 再关 LLM 连接池 → 最后关 DB。DB 必须最后关。
    """
    logger.info("[Lifespan] 开始关闭 | Shutting down...")

    await _stop_cleanup_task()

    from task_planner.core.db import close_db
    from task_planner.infrastructure.llm.client import aclose_llm_client
    from task_planner.services.stream.cleaner import state_cleaner

    await _shutdown_quietly("UI 状态清理器 | state_cleaner", state_cleaner.stop)
    await _shutdown_quietly("LLM 客户端 | LLM client", aclose_llm_client)
    await _shutdown_quietly("数据库连接 | database", close_db)

    logger.info("[Lifespan] 关闭完成 | Shutdown complete")


def register_lifespan_handlers() -> None:
    """把启动/关闭钩子挂到 FastAPI 实例上。幂等，可重复调用。

    ⚠️ 钩子必须挂在 ``app.server.router`` 上，而不是 ``app.server`` 上：
       FastAPI 自身没有 add_event_handler，该方法定义在内部的 Starlette
       Router 上（fastapi 0.165 / starlette 实测）。写成
       ``app.server.add_event_handler(...)`` 会在启动时直接 AttributeError。

    ⚠️ 不使用 ``app.server.on_event("startup")`` 装饰器形式：FastAPI 已将其
       标记为 deprecated（官方建议改用 lifespan）。这里用 router 层的
       add_event_handler，等价且不触发弃用警告。

    ⚠️ uvicorn --reload 时子进程会重新导入本模块，这里用模块级标志位
       保证只注册一次，避免同一钩子被叠加注册多次。
    """
    global _hooks_registered

    if _hooks_registered:
        return

    router = app.server.router
    router.add_event_handler("startup", _on_startup)
    router.add_event_handler("shutdown", _on_shutdown)
    _hooks_registered = True


def _validate_port(port_value: Any) -> int:
    """把配置项校验成合法端口号，失败抛 ValueError（由调用方决定退出方式）。"""
    try:
        port = int(port_value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"DASH_PORT 无法转换为整数 | cannot convert to int: {port_value!r}") from e
    if port not in VALID_PORT_RANGE:
        raise ValueError(f"DASH_PORT={port} 超出合法范围 [1-65535] | out of range [1-65535]")
    return port


def main() -> None:
    """进程入口：校验配置 → 注册生命周期钩子 → 交给 uvicorn 托管。"""
    host = str(_hub.dev.DASH_HOST)
    debug = bool(_hub.dev.DEBUG)

    try:
        port = _validate_port(_hub.dev.DASH_PORT)
    except ValueError as e:
        logger.error("[Startup] 配置校验失败 | Config validation failed: %s", e)
        sys.exit(1)

    register_lifespan_handlers()

    mode = "debug" if debug else "production"
    # 格式串 3 个占位符严格对应 mode / host / port。
    # 早期版本写成 "(%s) | starting (%s) | %s:%d" 传 (mode, mode, host, port)，
    # 数量虽对得上，但 mode 被重复打了两遍（中英文各一次），属冗余，已收敛。
    logger.info(
        "[Startup] Dash (FastAPI backend) 启动中 | %s 模式 | %s:%d | 配置抽屉=/config",
        mode,
        host,
        port,
    )

    uvicorn.run(
        app.server,
        host=host,
        port=port,
        reload=debug,
        log_level="debug" if debug else "info",
    )


__all__ = ["app", "main", "register_lifespan_handlers"]
