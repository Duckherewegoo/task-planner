"""security — 代码内置项"""
from ..entry import (
    ConfigEntry,
    Scope,
)
from ..registry import register_section
from ..section import ConfigSection


@register_section
class SecuritySection(ConfigSection):
    section = "security_code"

    def define(self):
        return (
            ConfigEntry(
                name="SENSITIVE_MASK_CHAR",
                value="*",
                purpose="敏感信息脱敏时使用的替换字符",
                scope=Scope.SYSTEM,
                type=str,
            ),
        )
