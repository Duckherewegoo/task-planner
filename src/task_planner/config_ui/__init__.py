"""
config_ui — 独立配置工具（可选套件）
═══════════════════════════════════════════════════════════════════════
与主应用完全隔离：
  - 不 import task_planner.main.ui
  - 不连 MongoDB / LLM
  - 只读写 config/user.yaml 和 .env

启动：task-planner-config
默认端口：8051（可用 CONFIG_UI_PORT 覆盖）
"""
from task_planner.config_ui.app import (
    app,
    main,
)

__all__ = ["app", "main"]
