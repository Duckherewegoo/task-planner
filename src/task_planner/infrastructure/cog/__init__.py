"""
cog — 配置中枢。
外部只允许:
    from task_planner.infrastructure.cog import hub
其余内部模块一律视为私有。
"""
from pathlib import Path

# 1) 触发所有内置 section 注册（副作用 import）
from . import sections  # noqa: F401

# 2) 组装 hub
from .entry import ConfigEntry, Role, Scope
from .hub import ConfigHub
from .registry import register_section
from .section import ConfigSection

_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"

hub: ConfigHub = ConfigHub().bootstrap(
    schema_path=_CONFIG_DIR / "schema.yaml",
    user_path=_CONFIG_DIR / "user.yaml",
)

__all__ = [
    "hub",               # 主入口
    "register_section",  # 扩展点
    "ConfigEntry",       # 加新条目时用
    "ConfigSection",     # 自定义分组时用
    "Scope",
    "Role",
]
