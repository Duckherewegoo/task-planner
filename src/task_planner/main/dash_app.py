"""
dash_app.py — 【兼容壳】
实现已迁至 task_planner.main.ui 包。
保留此文件仅为向后兼容，新代码请直接 import 自 task_planner.main.ui
"""
from __future__ import annotations

from task_planner.main.ui import (
    app,
    build_layout,
    main,
)

__all__ = ["app", "main", "build_layout"]
