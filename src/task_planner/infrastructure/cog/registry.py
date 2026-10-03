"""
registry.py — Section 类注册表 + @register_section 装饰器。
只存"类"，不存实例、不存值。
"""
from __future__ import annotations

from typing import (
    Dict,
    Tuple,
    Type,
)

from .section import ConfigSection


class SectionRegistry:
    def __init__(self) -> None:
        self._sections: Dict[str, Type[ConfigSection]] = {}

    def register(self, cls: Type[ConfigSection]) -> Type[ConfigSection]:
        if not (isinstance(cls, type) and issubclass(cls, ConfigSection)):
            raise TypeError(f"只能注册 ConfigSection 子类，收到 {cls!r}")
        name = cls.section
        if name in self._sections:
            raise KeyError(f"section 名冲突: {name}")
        self._sections[name] = cls
        return cls

    def all(self) -> Tuple[Type[ConfigSection], ...]:
        return tuple(self._sections.values())

    def clear(self) -> None:
        """仅供测试使用"""
        self._sections.clear()


# ── 全局单例 + 装饰器 ──
_SECTION_REGISTRY = SectionRegistry()


def register_section(cls: Type[ConfigSection]) -> Type[ConfigSection]:
    """装饰器：类定义处声明，import 时自动进全局注册表"""
    return _SECTION_REGISTRY.register(cls)

def get_registered_sections() -> Tuple[Type[ConfigSection], ...]:
    """
    返回所有已注册的 Section 类（对外只读接口）。

    hub.bootstrap / hub.reload 用这个接口遍历，
    不直接碰 _SECTION_REGISTRY（保持其私有语义）。
    """
    return _SECTION_REGISTRY.all()

__all__ = [
    "SectionRegistry",
    "register_section",
    "get_registered_sections",   # ← 新增
]
