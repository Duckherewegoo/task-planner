"""
store.py — 内存配置仓库。只干 CRUD，不判权限、不读文件。
"""

from __future__ import annotations

from collections.abc import Iterator
from threading import RLock

from .entry import ConfigEntry


class ConfigStore:
    """内存仓库。四个动词：create / read / update / delete。

    ⚠️ 锁语义：_lock 保护的是 ``_data`` 这个**名字绑定**。
    replace_all 换绑后，旧 dict 上的并发迭代器不受本锁保护——
    所以 read 路径必须拿到 tuple 快照后立刻释放锁（见 all）。
    """

    def __init__(self) -> None:
        self._data: dict[str, ConfigEntry] = {}
        self._lock = RLock()

    def create(self, entry: ConfigEntry) -> None:
        with self._lock:
            if entry.name in self._data:
                raise KeyError(f"已存在: {entry.name}")
            self._data[entry.name] = entry

    def read(self, name: str) -> ConfigEntry:
        with self._lock:
            try:
                return self._data[name]
            except KeyError:
                raise KeyError(f"未注册: {name}") from None

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._data

    def all(self) -> tuple[ConfigEntry, ...]:
        # 快照语义：锁内拷贝成 tuple，锁外遍历安全
        with self._lock:
            return tuple(self._data.values())

    def update(self, entry: ConfigEntry) -> None:
        with self._lock:
            if entry.name not in self._data:
                raise KeyError(f"未注册: {entry.name}")
            self._data[entry.name] = entry

    def delete(self, name: str) -> None:
        with self._lock:
            if name not in self._data:
                raise KeyError(f"未注册: {name}")
            del self._data[name]

    def replace_all(self, other: ConfigStore) -> None:
        """原子替换内部数据。

        ✅ 原地变更：``clear()`` + 逐条 ``create`` 语义，**不换 dict 实例**。
        历史实现是 ``self._data = dict(other._data)``（换引用）——那会让
        reload 前已取得的迭代器和持有旧 dict 引用的调用方静默读到旧配置。

        ⚠️ 仍是"换内容"而非"逐条 diff"：已缓存的 ConfigSession 若缓存的是
        **entry 对象本身**，reload 后仍指向旧 entry。Session 必须每次穿透
        read(name)，不能缓存 entry（见 session.py 契约）。
        """
        with self._lock:
            self._data.clear()
            for name, entry in other._data.items():
                self._data[name] = entry

    def clear(self) -> int:
        """清空所有条目，返回清除数量。保留锁对象与 dict 实例。"""
        with self._lock:
            n = len(self._data)
            self._data.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __iter__(self) -> Iterator[ConfigEntry]:
        # 走 all() 的快照，避免调用方在锁外遍历活 dict
        return iter(self.all())
