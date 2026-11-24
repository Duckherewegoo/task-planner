"""app.py — Dash app 实例 + main()，Quart + Hypercorn 版。"""

import asyncio
import os
import sys
from typing import Any

import dash
import dash_cytoscape as cyto

from task_planner.config_ui.callbacks import register_config_callbacks
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import VALID_PORT_RANGE
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.ui")

cyto.load_extra_layouts()


app = dash.Dash(
    __name__,
    backend="quart",  # ← 唯一改的地方 1
    title="🧠 通用任务规划助手",
    assets_folder="./assets",
    suppress_callback_exceptions=True,
)

os.environ["DASH_DISABLE_VERSION_CHECK"] = str(_hub.dev.DASH_DISABLE_VERSION_CHECK)

register_config_callbacks(app, with_drawer=True)

# 暴露给 Hypercorn 的 ASGI 入口（命令行 hypercorn task_planner.main.app:server）
server = app.server  # ← 唯一改的地方 2

_cleanup_task: asyncio.Task | None = None
_SESSION_CLEANUP_INTERVAL = 300


def _register_lifespan_handlers() -> None:
    @app.server.before_serving  # ← 改的地方 3
    async def _on_startup() -> None:
        global _cleanup_task
        from task_planner.core.session import cleanup_stale_sessions

        async def _cleanup_loop() -> None:
            while True:
                try:
                    await asyncio.sleep(_SESSION_CLEANUP_INTERVAL)
                    cleaned = await cleanup_stale_sessions()
                    if cleaned:
                        logger.info("[Lifespan] Cleaned %d stale sessions", cleaned)
                except asyncio.CancelledError:
                    logger.info("[Lifespan] Cleanup loop cancelled, exiting.")
                    break
                except Exception as exc:
                    logger.error("[Lifespan] Cleanup loop error: %s", exc)

        _cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info("[Lifespan] Session cleanup task started")

    @app.server.after_serving  # ← 改的地方 4
    async def _on_shutdown() -> None:
        from task_planner.core.db import close_db
        from task_planner.services.stream.cleaner import state_cleaner

        if _cleanup_task is not None:
            _cleanup_task.cancel()
            try:
                await asyncio.wait_for(_cleanup_task, timeout=5.0)
            except TimeoutError, asyncio.CancelledError:
                pass

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
    """内网开发用——直接调 hypercorn.asyncio.serve。"""

    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    host = _hub.dev.DASH_HOST
    debug = _hub.dev.DEBUG

    try:
        port = _validate_port(_hub.dev.DASH_PORT)
    except ValueError as e:
        logger.error("[Startup] 配置校验失败: %s", e)
        sys.exit(1)

    _register_lifespan_handlers()

    config = Config()
    config.bind = [f"{host}:{port}"]
    config.reload = debug
    config.loglevel = "debug" if debug else "info"

    mode = "debug" if debug else "production"
    logger.info(
        "[Startup] Dash (Quart + Hypercorn) starting (%s) | %s:%d | 配置抽屉=/config",
        mode,
        host,
        port,
    )

    asyncio.run(serve(server, config))


__all__ = ["app", "server", "main"]
