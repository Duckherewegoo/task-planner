"""
section.py — ConfigSection 抽象基类。
子类只负责实现 define()，不管存储/权限/IO。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from .entry import ConfigEntry


class ConfigSection(ABC):
    """一组配置的抽象声明。"""

    section: str = "base"  # 子类覆盖，作为分组名

    @abstractmethod
    def define(self) -> Tuple["ConfigEntry", ...]:
        """声明本组所有配置项，返回不可变元组"""
        raise NotImplementedError

    def register_into(self, registry) -> None:
        """便捷方法：直接把本组注册到 ConfigRegistry（可选使用）"""
        for entry in self.define():
            if not registry.has(entry.name):
                registry.create(entry)
