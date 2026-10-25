"""app.py — 配置工具 Dash 实例与启动入口。

本次变更：
  - register() → register_config_callbacks(app, with_drawer=False)
  - 布局从 build_layout() 拆成 build_config_panel() + 卡片外壳
  - 独立 CLI 路径保留，作为运维降级路径
"""
import os
import sys

import dash

from task_planner.config_ui.callbacks import register_config_callbacks
from task_planner.config_ui.layout import build_layout
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.config_ui")

# 独立端口（跟主应用 8050 错开）
_DEFAULT_PORT = 8051
_DEFAULT_HOST = "127.0.0.1"


def _make_app() -> dash.Dash:
    app = dash.Dash(
        __name__,
        title="⚙️ Task Planner 配置",
        suppress_callback_exceptions=True,
    )
    app.layout = build_layout()
    register_config_callbacks(app, with_drawer=False)
    return app


app = _make_app()


def main() -> None:
    """CLI 入口点：task-planner-config"""
    host = os.getenv("CONFIG_UI_HOST", _DEFAULT_HOST)
    try:
        port = int(os.getenv("CONFIG_UI_PORT", _DEFAULT_PORT))
    except ValueError:
        print("[ConfigUI] CONFIG_UI_PORT 必须为整数", file=sys.stderr)
        sys.exit(1)

    logger.info("[ConfigUI] 启动配置工具 | http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
