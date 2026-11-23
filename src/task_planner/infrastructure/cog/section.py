"""section.py — ConfigSection 抽象基类。
子类只负责实现 define()，不管存储/权限/IO。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .entry import ConfigEntry
from dataclasses import replace


class ConfigSection(ABC):
    """一组配置的抽象声明。

    子类通过 ``section`` 类属性声明分组名，通过 ``define()`` 返回本组所有
    ``ConfigEntry``。分组名会在装配时自动注入到每条 entry 的 ``section``
    字段（见 ``register_into``），因此 ``define()`` 里不必手动传
    ``section=self.section``。

    基类不感知存储实现——``register_into`` 的 ``registry`` 参数只要求有
    ``has(name)`` 和 ``create(entry)`` 两个方法，``ConfigStore`` 和
    ``ConfigRegistry`` 都满足。
    """

    section: str = "base"  # 子类覆盖，作为分组名

    @abstractmethod
    def define(self) -> tuple[ConfigEntry, ...]:
        """声明本组所有配置项，返回不可变元组。

        返回的 entry 的 ``section`` 字段可以是占位值（默认 ``"common"``），
        装配时会被本类的 ``section`` 类属性覆盖。
        """
        raise NotImplementedError

    def register_into(self, registry) -> None:
        """把本组条目写入 ``registry``，跳过已存在的同名项。

        - 已存在的条目（典型来源：schema.yaml 已有定义）保持原样，
          包括其 ``section``——即 YAML 的分组优先于代码内置分组。
        - 写入前用 ``dataclasses.replace`` 把 ``section`` 统一设为本类的
          ``section`` 类属性，保证代码内置项归到正确分组。
        - ``ConfigEntry`` 是 frozen 的，``replace`` 返回新实例，不修改原值。
        """
        for entry in self.define():
            if not registry.has(entry.name):
                registry.create(replace(entry, section=self.section))
