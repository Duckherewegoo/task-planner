"""base — 代码内置的基础配置。

分两类，判定标准是"上线后运维该不该有权力改它"：

- **编译期常量**（VERSION）：装完才知道，YAML 里写死反而成"可篡改的假版本"，
  审计失去意义。只在这里声明，YAML 里刻意不写。
- **协议常量**（MOCK_RESPONSE_PREFIX）：mock 模块发、`json_utils` 认，
  是代码内部契约，不是运维旋钮。唯一真相源是 `llm.mocks.MOCK_RESPONSE_PREFIX`，
  这里 import 后转发，**不写字面量**——否则改一处漏一处会让
  `USE_MOCK_LLM=true` 的测试静默假绿。
"""

from __future__ import annotations

from importlib.metadata import version

from ..entry import ConfigEntry, Scope
from ..registry import register_section
from ..section import ConfigSection


def _detect_version() -> str:
    """已安装版本；未在开发模式安装时退化为 "0+unknown" 而不是抛异常。

    这里不能用裸 ``version("task-planner")``：一旦 import cog 就抛
    PackageNotFoundError，整个配置系统（含 config_ui 独立进程）直接起不来，
    而版本号缺失本不该是致命错误。
    """
    try:
        return version("task-planner")
    except Exception:  # PackageNotFoundError 等
        return "0+unknown"


@register_section
class BaseSection(ConfigSection):
    section = "base_code"

    def define(self) -> tuple[ConfigEntry, ...]:
        return (
            ConfigEntry(
                name="VERSION",
                value=_detect_version(),
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
