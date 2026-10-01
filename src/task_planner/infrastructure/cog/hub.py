"""
hub.py — 配置系统唯一对外门面。
职责只有：装配 / 转发 / 提供角色视图。
✋ 不实现 CRUD、不判权限、不读文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from .entry import ConfigEntry, Role
from .guard import ConfigGuard
from .loader import ConfigLoader
from .registry import _SECTION_REGISTRY
from .session import ConfigSession
from .store import ConfigStore


class ConfigHub:
    def __init__(self) -> None:
        self._store = ConfigStore()
        self._guard = ConfigGuard()
        self._sessions: dict[Role, ConfigSession] = {}

    # ---------- 装配 ----------
    def bootstrap(
        self, schema_path: Path, user_path: Optional[Path] = None
    ) -> "ConfigHub":
        # 1) YAML → store
        ConfigLoader(self._store).load(schema_path, user_path)
        # 2) Python @register_section → store（YAML 已存在的不覆盖）
        for cls in _SECTION_REGISTRY.all():
            for entry in cls().define():
                if not self._store.has(entry.name):
                    self._store.create(entry)
        return self

    # ---------- 角色视图 ----------
    def as_role(self, role: Role) -> ConfigSession:
        if role not in self._sessions:
            self._sessions[role] = ConfigSession(self._store, self._guard, role)
        return self._sessions[role]

    @property
    def dev(self) -> ConfigSession:
        return self.as_role(Role.DEVELOPER)

    @property
    def user(self) -> ConfigSession:
        return self.as_role(Role.USER)

    # ---------- 只读 ----------
    def snapshot(self) -> Tuple[ConfigEntry, ...]:
        return self._store.all()

    def __len__(self) -> int:
        return len(self._store)
