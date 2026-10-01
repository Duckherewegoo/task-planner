"""app.py — Dash app 实例 + DB 初始化 + main()"""
from __future__ import annotations

import asyncio
import os
import sys

import dash
import dash_cytoscape as cyto

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import VALID_PORT_RANGE
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.dash_app")

cyto.load_extra_layouts()


def _bootstrap_db() -> None:
    """模块导入时执行一次 DB 初始化"""
    from task_planner.core.database import init_db

    try:
        asyncio.run(init_db())
        logger.info("[Dash] ✅ 数据库初始化完成")
    except Exception as e:
        logger.warning("[Dash] ⚠️ 数据库初始化失败，将在首次请求时重试: %s", e)


_bootstrap_db()


# ── app 实例（唯一） ──
app = dash.Dash(
    __name__,
    title="🧠 通用任务规划助手",
    assets_folder="../assets",       # 指向 main/assets
    suppress_callback_exceptions=True,
)

os.environ["DASH_DISABLE_VERSION_CHECK"] = str(_hub.dev.DASH_DISABLE_VERSION_CHECK)


# ── 启动入口 ──
def _validate_port(port_value) -> int:
    try:
        port = int(port_value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"DASH_PORT 无法转换为整数: {port_value!r}") from e
    if port not in VALID_PORT_RANGE:
        raise ValueError(f"DASH_PORT={port} 超出合法范围 [1-65535]")
    return port


def main() -> None:
    from task_planner.utils.good_addons import boost

    host = _hub.dev.DASH_HOST
    debug = _hub.dev.DEBUG

    try:
        port = _validate_port(_hub.dev.DASH_PORT)
    except ValueError as e:
        logger.error("[Startup] 配置校验失败: %s", e)
        sys.exit(1)

    boost(
        app=app,
        graceful_shutdown=True,
        memory_watchdog=not debug,
    )

    mode = "debug" if debug else "production"
    # ✅ 修复：WERKZEUG_RUN_MAIN 从环境变量读，不硬编码
    is_reloader = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    process_tag = " [reloader]" if is_reloader else ""

    logger.info(
        "[Startup] Dash starting (%s%s) | %s:%d",
        mode, process_tag, host, port,
    )

    app.run(debug=debug, host=host, port=port)


__all__ = ["app", "main"]
