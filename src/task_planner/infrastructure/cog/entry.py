"""
entry.py — 配置条目的数据结构。
只负责定义"一条配置长什么样"，不含任何行为。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class Scope(Enum):
    """谁能改这条配置"""
    USER = "user"            # 终端用户可改（UI 偏好等）
    DEVELOPER = "developer"  # 仅开发者可改（超时、密钥等）
    SYSTEM = "system"        # 运行时锁定，谁都不能改


class Role(Enum):
    """操作者身份"""
    USER = "user"
    DEVELOPER = "developer"


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    name: str            # 键（点号访问 / dict key）
    value: Any           # 当前值
    purpose: str         # 一句话用途（必填，代替注释）
    scope: Scope         # 修改权限档位
    type: type           # 期望 Python 类型
    default: Any = None  # 出厂默认（reset 用）
    section: str = "common"
    secret: bool = False  # True → 日志/UI 脱敏
    from_env: bool = False        # ← 新增：值来自环境变量
    env_name: str = ""            # ← 新增：实际读取的变量名（默认=name）

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ConfigEntry.name 不能为空")
        if not self.purpose:
            raise ValueError(f"{self.name}: purpose 必填，别用注释糊弄")
        if not isinstance(self.scope, Scope):
            raise TypeError(f"{self.name}: scope 必须是 Scope 枚举")
        if self.secret and not self.from_env:
            raise ValueError(f"{self.name}: secret 字段必须 from_env")

    def with_value(self, v: Any) -> "ConfigEntry":
        return replace(self, value=v)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": "<secret>" if self.secret else self.value,
            "purpose": self.purpose,
            "scope": self.scope.value,
            "type": self.type.__name__,
            "section": self.section,
            "secret": self.secret,
            "from_env": self.from_env,
        }
