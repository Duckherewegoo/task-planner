"""
registry.py — Section 类注册表 + @register_section 装饰器。
只存"类"，不存实例、不存值。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .section import ConfigSection


class SectionRegistry:
    """Section 类注册表。

    ⚠️ 这里**不能**直接 `from .section import ConfigSection`：section 模块
    本身要 import register_section，直连会形成循环导入。改用鸭子类型校验
    （认 `define` 可调用 + `section` 非空字符串），TYPE_CHECKING 下仍给出
    完整类型提示。
    """

    def __init__(self) -> None:
        self._sections: dict[str, type[ConfigSection]] = {}

    def register(self, cls: type[ConfigSection]) -> type[ConfigSection]:
        ok = (
            isinstance(cls, type)
            and callable(getattr(cls, "define", None))
            and isinstance(getattr(cls, "section", None), str)
            and cls.section != ""
        )
        if not ok:
            raise TypeError(f"只能注册带 section 名与 define() 的 ConfigSection 子类，收到 {cls!r}")
        name: str = cls.section
        if name in self._sections:
            raise KeyError(f"section 名冲突: {name}（后者被忽略，请检查类名重复）")
        self._sections[name] = cls
        return cls

    def all(self) -> tuple[type[ConfigSection], ...]:
        return tuple(self._sections.values())

    def clear(self) -> None:
        """仅供测试使用"""
        self._sections.clear()


# ── 全局单例 + 装饰器 ──
_SECTION_REGISTRY = SectionRegistry()


def register_section(cls: type[ConfigSection]) -> type[ConfigSection]:
    """装饰器：类定义处声明，import 时自动进全局注册表"""
    return _SECTION_REGISTRY.register(cls)


def get_registered_sections() -> tuple[type[ConfigSection], ...]:
    """
    返回所有已注册的 Section 类（对外只读接口）。

    hub 的 _populate / reload 用这个接口遍历，
    不直接碰 _SECTION_REGISTRY（保持其私有语义）。

    ⚠️ **首次调用时自动 import ``cog.sections``**：这是本包的坑位。
       ``sections`` 子包里的每个模块都要 import ``register_section``，
       而 ``registry`` 又被 ``cog/__init__`` 在 ``section`` 之前导入——
       若依赖"调用方记得 import cog.sections"，漏掉时所有代码内置项
       会静默消失，只剩 YAML 那部分，且不报任何错。
       放在这里 import 可打破环：此时 registry / section 均已就绪。
    """
    import importlib

    if not _SECTION_REGISTRY._sections:
        importlib.import_module("cog.sections")
    return _SECTION_REGISTRY.all()


__all__ = [
    "SectionRegistry",
    "register_section",
    "get_registered_sections",
]
