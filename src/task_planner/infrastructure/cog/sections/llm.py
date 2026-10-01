"""llm — 代码内置项"""
from ..entry import ConfigEntry, Scope
from ..registry import register_section
from ..section import ConfigSection


@register_section
class LLMSection(ConfigSection):
    section = "llm_code"

    def define(self):
        return (
            ConfigEntry(
                name="LLM_STREAM",
                value=False,
                purpose="是否启用流式返回（暂未实现，预留）",
                scope=Scope.DEVELOPER,
                type=bool,
            ),
        )
