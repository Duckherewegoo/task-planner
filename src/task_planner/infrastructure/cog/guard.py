"""
guard.py — 权限策略。无状态，可单测。
"""
from __future__ import annotations

from .entry import (
    ConfigEntry,
    Role,
    Scope,
)


class ConfigGuard:
    """只回答"角色 X 能不能改条目 Y"。"""

    _WRITE_RULES = {
        Role.USER:      frozenset({Scope.USER}),
        Role.DEVELOPER: frozenset({Scope.USER, Scope.DEVELOPER}),
    }

    def can_write(self, entry: ConfigEntry, role: Role) -> bool:
        return entry.scope in self._WRITE_RULES.get(role, frozenset())

    def assert_writable(self, entry: ConfigEntry, role: Role) -> None:
        if not self.can_write(entry, role):
            raise PermissionError(
                f"角色 {role.value} 无权修改 {entry.name}（scope={entry.scope.value}）"
            )

    def can_delete(self, entry: ConfigEntry, role: Role) -> bool:
        if entry.scope == Scope.SYSTEM:
            return False
        if entry.scope == Scope.DEVELOPER:
            return role == Role.DEVELOPER
        return True
