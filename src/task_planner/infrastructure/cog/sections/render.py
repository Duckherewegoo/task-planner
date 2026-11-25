"""唯一真相源改为 YAML，代码内不再写字面量。

本 section 仅作为"兜底声明"存在：若 schema.yaml 未声明同名条目，
这里的值会被注入 store；若 YAML 已声明（正常情况），register_into
会跳过并打 WARNING。改动运行时值请改 config/schema.yaml，不要改这里。
"""

from __future__ import annotations

from ..entry import ConfigEntry
from ..registry import register_section
from ..section import ConfigSection


@register_section
class RenderSection(ConfigSection):
    section = "render_code"

    def define(self) -> tuple[ConfigEntry, ...]:
        """本组字段全部在 schema.yaml 声明，代码侧不再重复。"""
        return ()
