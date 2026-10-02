"""render — 代码内置项"""
from ..entry import (
    ConfigEntry,
    Scope,
)
from ..registry import register_section
from ..section import ConfigSection


@register_section
class RenderSection(ConfigSection):
    section = "render_code"

    def define(self):
        return (
            ConfigEntry(
                name="RENDER_BACKGROUND",
                value="#ffffff",
                purpose="导出图片背景色",
                scope=Scope.USER,
                type=str,
            ),
        )
