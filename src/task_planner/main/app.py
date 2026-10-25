"""app.py — Dash app 实例 + main()。

本次变更：
  - 引入配置面板回调注册（含 URL 驱动 Offcanvas）。
  - 零额外中间件、零额外 Flask 实例，高并发路径与原实现完全一致。
"""
import os
import sys

import dash
import dash_cytoscape as cyto

from task_planner.config_ui.callbacks import register_config_callbacks
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import VALID_PORT_RANGE
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.ui")

cyto.load_extra_layouts()


# ═══════════════════════════════════════════════════════════════════
# DB 初始化——移除模块级 asyncio.run（原有因果链注释原样保留）
# ═══════════════════════════════════════════════════════════════════
# 原实现：模块 import 时 asyncio.run(init_db())
# → asyncio.run 创建 loop A → Motor client 绑定 loop A → loop A 关闭
# → 真实请求用 loop B → "Event loop is closed"
#
# 现在：Motor 换成同步 pymongo（见 core/db/client.py），无 loop 依赖。
# DB 改为首次请求时懒初始化——get_db() 内部自动 init_db()。


# ── 主 app 实例（唯一） ──
app = dash.Dash(
    __name__,
    title="🧠 通用任务规划助手",
    assets_folder="./assets",
    suppress_callback_exceptions=True,
)

os.environ["DASH_DISABLE_VERSION_CHECK"] = str(_hub.dev.DASH_DISABLE_VERSION_CHECK)


# ═══════════════════════════════════════════════════════════════════
# 注册配置面板回调
# ═══════════════════════════════════════════════════════════════════
# 必须在 app.layout 赋值之前——Dash 的 CallbackGraph 在 layout 确定时构建，
# 晚于 layout 注册的回调会触发「Output 引用不存在组件」异常。
# with_drawer=True：主应用有 dcc.Location 和抽屉组件，注册 URL→开关逻辑。


register_config_callbacks(app, with_drawer=True)


# ── 启动入口（与原实现完全一致，零 WSGI 改动） ──
def _validate_port(port_value) -> int:
    try:
        port = int(port_value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"DASH_PORT 无法转换为整数: {port_value!r}") from e
    if port not in VALID_PORT_RANGE:
        raise ValueError(f"DASH_PORT={port} 超出合法范围 [1-65535]")
    return port


def main() -> None:
    from task_planner.core.db import close_db
    from task_planner.utils.good_addons import SHUTDOWN, boost

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

    SHUTDOWN.register(close_db, priority=-100)
    from task_planner.infrastructure.async_runtime import shutdown as async_runtime_shutdown
    SHUTDOWN.register(async_runtime_shutdown, priority=-200)

    mode = "debug" if debug else "production"
    is_reloader = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    process_tag = " [reloader]" if is_reloader else ""

    logger.info("[Startup] Dash starting (%s%s) | %s:%d | 配置抽屉=/config",
                mode, process_tag, host, port)

    app.run(debug=debug, host=host, port=port)


__all__ = ["app", "main"]
