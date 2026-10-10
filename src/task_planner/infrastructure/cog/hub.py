"""
hub.py — 配置系统唯一对外门面。
职责只有：装配 / 转发 / 提供角色视图。
✋ 不实现 CRUD、不判权限、不读文件。
"""
from __future__ import annotations

from pathlib import Path

from .entry import ConfigEntry, Role
from .guard import ConfigGuard
from .loader import ConfigLoader
from .registry import get_registered_sections
from .session import ConfigSession
from .store import ConfigStore


class ConfigHub:
    def __init__(self) -> None:
        self._store = ConfigStore()
        self._guard = ConfigGuard()
        self._sessions: dict[Role, ConfigSession] = {}

    # ---------- 装配 ----------
    def bootstrap(
        self, schema_path: Path, user_path: Path | None = None
    ) -> ConfigHub:
        # 1) YAML → store
        ConfigLoader(self._store).load(schema_path, user_path)
        # 2) Python @register_section → store（YAML 已存在的不覆盖）
        for cls in get_registered_sections():
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
    def snapshot(self) -> tuple[ConfigEntry, ...]:
        return self._store.all()

    # ---------- 重载 ----------
    def reload(
        self,
        schema_path: Path,
        user_path: Path | None = None,
    ) -> int:
        """
        从磁盘重新加载配置。

        - 清空 store（原地清空，不换实例，现有 session 继续可用）
        - 重跑 ConfigLoader（读 schema.yaml + user.yaml）
        - 重跑所有 @register_section 的 section
        - 返回最终条目数

        ⚠️ 注意：
          - 已缓存的 ConfigEntry 实例会被替换，持有旧 entry 引用的代码
            需要重新从 hub 读取（正常用法下没人持有 entry 引用）
          - 不会重载 from_env 字段（那些从 os.environ 读，运行时不可改）
        """
        self._store.clear()

        # 1) 从 YAML 重载
        ConfigLoader(self._store).load(schema_path, user_path)

        # 2) 从 @register_section 重载（YAML 已存在的不覆盖）
        for cls in get_registered_sections():
            for entry in cls().define():
                if not self._store.has(entry.name):
                    self._store.create(entry)

        total = len(self._store)
        return total

    def __len__(self) -> int:
        return len(self._store)
