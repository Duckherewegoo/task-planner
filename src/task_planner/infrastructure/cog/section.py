"""section.py — ConfigSection 抽象基类。
子类只负责实现 define()，不管存储/权限/IO。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .entry import ConfigEntry

logger = logging.getLogger("task_planner.cog.section")


class ConfigSection(ABC):
    """一组配置的抽象声明。

    子类通过 ``section`` 类属性声明分组名，通过 ``define()`` 返回本组所有
    ``ConfigEntry``。分组名会在装配时自动注入到每条 entry 的 ``section``
    字段（见 ``register_into``），因此 ``define()`` 里不必手动传
    ``section=self.section``。

    装配优先级（由 ConfigHub._populate 保证）：
        schema.yaml  >  代码内置（本类）
    同名条目以 YAML 为准，本类的声明被跳过并记 warning。
    """

    section: str = "base"  # 子类覆盖，作为分组名

    @abstractmethod
    def define(self) -> tuple[ConfigEntry, ...]:
        """声明本组所有配置项，返回不可变元组。

        返回的 entry 的 ``section`` 字段可以是占位值（默认 ``"common"``），
        装配时会被本类的 ``section`` 类属性覆盖。
        """
        raise NotImplementedError

    def register_into(self, registry: Any) -> None:
        """把本组条目写入 ``registry``，跳过已存在的同名项。

        - 已存在的条目（典型来源：schema.yaml 已有定义）保持原样，
          包括其 ``section`` 与 ``scope``——YAML 的分组/作用域优先于代码内置。
        - 写入前用 ``dataclasses.replace`` 把 ``section`` 统一设为本类的
          ``section`` 类属性，保证代码内置项归到正确分组。
        - ``ConfigEntry`` 是 frozen 的，``replace`` 返回新实例，不修改原值。
        - 同名冲突**不再静默**：记 warning。这是"代码里改了值却不生效"
          这类问题的唯一可观测出口（CI 侧另有 name 全局唯一断言兜底）。
        """
        for entry in self.define():
            if registry.has(entry.name):
                logger.warning(
                    "[Cog] 代码内置项 %s.%s 被同名条目覆盖，已跳过。"
                    "请确认 schema.yaml 是否重复声明；若要仅代码内置，"
                    "请从 YAML 删除该条。",
                    self.section,
                    entry.name,
                )
                continue
            registry.create(replace(entry, section=self.section))
