"""app.py — Dash app 实例 + main()，FastAPI backend 版。

本次变更：
  - 后端切换为 FastAPI：Dash(__name__, backend="fastapi")
  - 启动方式改为 uvicorn，不再走 app.run() / Werkzeug
  - 关闭钩子改挂到 FastAPI 的 shutdown 事件
  - 移除 boost()、WERKZEUG_RUN_MAIN、async_runtime 后台 loop
    （FastAPI backend 下回调本身跑在 uvicorn 的持久 loop 上，
     create_task 创建的任务天然跨回调存活，无需独立后台 loop）
"""

import asyncio
import os
import sys
from typing import Any

import dash
import dash_cytoscape as cyto
import uvicorn

from task_planner.config_ui.callbacks import register_config_callbacks
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import VALID_PORT_RANGE
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.utils.good_addons.good import boost

logger = get_logger("task_planner.ui")

cyto.load_extra_layouts()


app = dash.Dash(
    __name__,
    backend="fastapi",
    title="🧠 通用任务规划助手",
    assets_folder="./assets",
    suppress_callback_exceptions=True,
)

# 增强：必须在 register_config_callbacks 之前
_boost_report = boost(
    app,
    backend="fastapi",
    app_kind="dash",
    log_level="DEBUG" if _hub.dev.DEBUG else "INFO",
    memory_watchdog=True,
    memory_limit_mb=2048,
)

os.environ["DASH_DISABLE_VERSION_CHECK"] = str(_hub.dev.DASH_DISABLE_VERSION_CHECK)

register_config_callbacks(app, with_drawer=True)

_cleanup_task: asyncio.Task[None] | None = None
# 清理间隔（秒），可以挪到 hub.dev 里配置，这里先硬编码兜底
_SESSION_CLEANUP_INTERVAL = 300


def _register_lifespan_handlers() -> None:
    @app.server.on_event("startup")
    async def _on_startup() -> None:
        global _cleanup_task
        from task_planner.services.agent.session import cleanup_stale_sessions

        async def _cleanup_loop() -> None:
            while True:
                try:
                    await asyncio.sleep(_SESSION_CLEANUP_INTERVAL)
                    # 这里也可以读 _hub.dev.SESSION_CLEANUP_INTERVAL 动态调整
                    cleaned = await cleanup_stale_sessions()
                    if cleaned:
                        logger.info("[Lifespan] Cleaned %d stale sessions", cleaned)
                except asyncio.CancelledError:
                    logger.info("[Lifespan] Cleanup loop cancelled, exiting.")
                    break
                except Exception as exc:
                    # 防止单个异常导致循环退出
                    logger.error("[Lifespan] Cleanup loop error: %s", exc)

        _cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info("[Lifespan] Session cleanup task started")

    @app.server.on_event("shutdown")
    async def _on_shutdown() -> None:
        from task_planner.core.db import close_db
        from task_planner.services.stream.cleaner import state_cleaner

        global _cleanup_task

        # 1. 停清理循环
        if _cleanup_task is not None:
            _cleanup_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(_cleanup_task), timeout=5.0)
            except TimeoutError:
                logger.warning("[Lifespan] Cleanup loop did not stop within 5s")
            except asyncio.CancelledError:
                pass  # 预期内：cancel() 的结果，不算错误
            _cleanup_task = None

        # 2. 先停 state_cleaner，再关 DB
        await state_cleaner.stop()
        await close_db()
        logger.info("[Lifespan] Shutdown complete")


def _validate_port(port_value: Any) -> int:
    try:
        port = int(port_value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"DASH_PORT 无法转换为整数: {port_value!r}") from e
    if port not in VALID_PORT_RANGE:
        raise ValueError(f"DASH_PORT={port} 超出合法范围 [1-65535]")
    return port


def main() -> None:
    host = _hub.dev.DASH_HOST
    debug = _hub.dev.DEBUG

    try:
        port = _validate_port(_hub.dev.DASH_PORT)
    except ValueError as e:
        logger.error("[Startup] 配置校验失败: %s", e)
        sys.exit(1)

    _register_lifespan_handlers()

    mode = "debug" if debug else "production"
    logger.info(
        "[Startup] Dash (FastAPI backend) starting (%s) | %s:%d | 配置抽屉=/config",
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


__all__ = ["app", "main"]
