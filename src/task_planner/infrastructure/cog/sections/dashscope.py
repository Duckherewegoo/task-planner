"""dashscope — 代码内置项（YAML 里已声明的会被跳过）"""
from ..entry import (
    ConfigEntry,
    Scope,
)
from ..registry import register_section
from ..section import ConfigSection


@register_section
class DashScopeSection(ConfigSection):
    section = "dashscope_code"

    def define(self):
        return (
            ConfigEntry(
                name="DASHSCOPE_TIMEOUT",
                value=30,
                purpose="DashScope HTTP 连接超时(秒)，与 LLM_TIMEOUT 独立",
                scope=Scope.DEVELOPER,
                type=int,
            ),
        )
