"""mongo — 代码内置项"""

from ..entry import ConfigEntry, Scope
from ..registry import register_section
from ..section import ConfigSection


@register_section
class MongoSection(ConfigSection):
    section = "mongo_code"

    def define(self):
        return (
            ConfigEntry(
                name="MONGO_TIMEOUT_MS",
                value=5000,
                default=5000,
                purpose="MongoDB 操作超时(毫秒)（改动需重启进程生效）",
                scope=Scope.DEVELOPER,
                type=int,
            ),
        )
