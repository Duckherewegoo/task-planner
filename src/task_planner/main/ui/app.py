"""app.py — Dash app 实例 + main()"""

import os
import sys

import dash
import dash_cytoscape as cyto

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import VALID_PORT_RANGE
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.ui")

cyto.load_extra_layouts()


# ═══════════════════════════════════════════════════════════════════
#  DB 初始化——移除模块级 asyncio.run
# ═══════════════════════════════════════════════════════════════════
# ✅ 删除 _bootstrap_db 的完整因果链：
#
#   原实现：模块 import 时 asyncio.run(init_db())
#     → asyncio.run 创建 loop A
#     → Motor client 绑定 loop A
#     → loop A 关闭
#     → 真实请求用 loop B 访问 client → "Event loop is closed"
#
#   这是"asyncio.run 的 loop 生命周期（跑完即毁）"与"Motor client 的
#   生命周期（活到进程结束）"不匹配。
#
#   现在：Motor 换成同步 pymongo（见 core/db/client.py），无 loop 依赖。
#        DB 改为**首次请求时懒初始化**——get_db() 内部自动 init_db()。
#        第一个请求慢一次（~100ms 建连+ping+建索引），之后走缓存。
#
#   代价：启动日志里不再有"[Dash] ✅ 数据库初始化完成"——
#        改为首次请求时由 client.py 打 "[DB] ✅ MongoDB 连接成功"。


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
    from task_planner.utils.good_addons import SHUTDOWN, boost
    from task_planner.core.db import close_db

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

    # ✅ 注册 DB 关闭到 GracefulShutdown——
    #    priority=-100：在所有 handler 之后执行（DB 最后关，
    #    确保其他 handler 若需查 DB 时不至于用已关闭的连接）。
    #    GracefulShutdown.install() 由 boost() 内部调用。
    #    ⚠️ debug 模式下 boost 的 install() 会因 WERKZEUG_RUN_MAIN
    #       守卫跳过——那时由 client.py 模块级的 atexit.register(close_db)
    #       兜底。两处都调用 close_db，它已实现幂等。
    SHUTDOWN.register(close_db, priority=-100)

    mode = "debug" if debug else "production"
    is_reloader = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    process_tag = " [reloader]" if is_reloader else ""

    logger.info(
        "[Startup] Dash starting (%s%s) | %s:%d",
        mode, process_tag, host, port,
    )

    app.run(debug=debug, host=host, port=port)


__all__ = ["app", "main"]
