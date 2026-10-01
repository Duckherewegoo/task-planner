"""web — 代码内置项"""
from ..entry import ConfigEntry, Scope
from ..registry import register_section
from ..section import ConfigSection


@register_section
class WebSection(ConfigSection):
    section = "web_code"

    def define(self):
        return (
            ConfigEntry(
                name="GRADIO_SHOW_ERROR",
                value=True,
                purpose="Gradio 是否显示详细错误信息",
                scope=Scope.DEVELOPER,
                type=bool,
            ),
        )
