"""runtime — 代码内置项"""
from ..entry import (
    ConfigEntry,
    Scope,
)
from ..registry import register_section
from ..section import ConfigSection


@register_section
class RuntimeSection(ConfigSection):
    section = "runtime_code"

    def define(self):
        return (
            ConfigEntry(
                name="ASYNC_TASK_QUEUE_SIZE",
                value=100,
                purpose="异步任务队列最大长度",
                scope=Scope.DEVELOPER,
                type=int,
            ),
        )
