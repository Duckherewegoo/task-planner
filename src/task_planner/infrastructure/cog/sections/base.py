"""base — 代码内置的基础配置（与 YAML 互补，不重复）"""
from importlib.metadata import version

from ..entry import (
    ConfigEntry,
    Scope,
)
from ..registry import register_section
from ..section import ConfigSection


@register_section
class BaseSection(ConfigSection):
    section = "base_code"
    value = version("task-planner")   # 从已安装的包读
    def define(self):
        return (
            ConfigEntry(
                name="VERSION",
                value=self.value,
                purpose="构建版本号，运行时只读，用于日志和审计",
                scope=Scope.SYSTEM,
                type=str,
            ),
            ConfigEntry(
                name="MOCK_RESPONSE_PREFIX",
                value="[MOCK_LLM_RESPONSE]",
                purpose="Mock LLM 回复前缀，下游据此跳过 JSON 清洗",
                scope=Scope.SYSTEM,
                type=str,
            ),
        )
