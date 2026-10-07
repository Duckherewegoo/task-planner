"""
ui — Dash UI 包（拆分版）
═══════════════════════════════════════════════════════
对外导出：app / main / build_layout
"""
from .app import (
    app,
    main,
)

# 注册所有回调（副作用 import）
from .callbacks import register_all
from .layout import build_layout

app.layout = build_layout()
register_all()

__all__ = ["app", "main", "build_layout"]
