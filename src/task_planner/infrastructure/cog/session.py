"""
session.py — 一个会话 = 一个角色。写前问 guard，读直通 store。
"""
from __future__ import annotations

from typing import Any

from .entry import ConfigEntry, Role
from .guard import ConfigGuard
from .store import ConfigStore


class ConfigSession:
    def __init__(self, store: ConfigStore, guard: ConfigGuard, role: Role):
        self._store = store
        self._guard = guard
        self._role = role

    # ---- Read ----
    def get(self, name: str) -> Any:
        return self._store.read(name).value

    def entry(self, name: str) -> ConfigEntry:
        return self._store.read(name)

    def all(self) -> tuple[ConfigEntry, ...]:
        return self._store.all()

    # ---- Update ----
    def set(self, name: str, value: Any) -> None:
        entry = self._store.read(name)
        self._guard.assert_writable(entry, self._role)
        if not isinstance(value, entry.type):
            raise TypeError(
                f"{name} 期望 {entry.type.__name__}，收到 {type(value).__name__}"
            )
        self._store.update(entry.with_value(value))

    # ---- Create ----
    def add(self, entry: ConfigEntry) -> None:
        self._guard.assert_writable(entry, self._role)
        self._store.create(entry)

    # ---- Delete ----
    def remove(self, name: str) -> None:
        entry = self._store.read(name)
        if not self._guard.can_delete(entry, self._role):
            raise PermissionError(f"角色 {self._role.value} 无权删除 {name}")
        self._store.delete(name)

    # ---- 点号访问 ----
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self.get(name)
        except KeyError:
            raise AttributeError(f"未注册: {name}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self.set(name, value)
