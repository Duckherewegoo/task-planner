"""
hub.py — 配置系统唯一对外门面。
职责只有：装配 / 转发 / 提供角色视图。
✋ 不实现 CRUD、不判权限、不读文件。
"""

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

    # ---------- 装配 ----------
    def _populate(self, store: ConfigStore, schema_path: Path, user_path: Path | None) -> None:
        """把磁盘 + 代码内置的配置装入 ``store``。

        装配顺序（先到先得，后者不覆盖前者）：
                  1. ``ConfigLoader`` 读 schema.yaml（+ 可选 user.yaml 覆盖）→ store
                  2. 遍历所有 ``@register_section`` 的 section，逐个调 ``register_into``

        第 2 步中，已被第 1 步装入的同名条目会被 ``register_into`` 跳过，
        因此 YAML 中显式声明的条目（及其 ``section`` 分组、``scope`` 等）
        优先于代码内置项。代码内置项若只在 Python 里定义，则由
        ``register_into`` 自动注入所属 section 名。

        只操作传入的 ``store``，不碰 ``self._store``——``bootstrap`` 传当前
        store，``reload`` 传 staging store，后者成功后由 ``replace_all`` 替换。
        """
        ConfigLoader(store).load(schema_path, user_path)
        for cls in get_registered_sections():
            cls().register_into(store)

    def bootstrap(self, schema_path: Path, user_path: Path | None = None) -> ConfigHub:
        self._populate(self._store, schema_path, user_path)
        return self

    # ---------- 重载 ----------
    def reload(self, schema_path: Path, user_path: Path | None = None) -> int:
        """从磁盘重新加载配置（原子：构建 staging 成功后整体替换）。
        - 会重新读取 from_env 字段的当前环境变量值；
          先 load_dotenv(override=True) 再 reload 即可实现密钥热更新
        - 任何异常都不会污染当前 store
        - 替换是单次引用赋值，其他线程要么看到旧配置、要么看到新配置
        - 不换 store 实例，已缓存的 ConfigSession 继续有效
        """
        staging = ConfigStore()
        self._populate(staging, schema_path, user_path)
        self._store.replace_all(staging)
        return len(self._store)

    def __len__(self) -> int:
        return len(self._store)
