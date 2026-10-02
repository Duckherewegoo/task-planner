"""
store.py — 内存配置仓库。只干 CRUD，不判权限、不读文件。
"""
from __future__ import annotations

from threading import RLock
from typing import Dict, Iterator, Tuple

from .entry import ConfigEntry


class ConfigStore:
    """内存仓库。四个动词：create / read / update / delete。"""

    def __init__(self) -> None:
        self._data: Dict[str, ConfigEntry] = {}
        self._lock = RLock()

    # ---- Create ----
    def create(self, entry: ConfigEntry) -> None:
        with self._lock:
            if entry.name in self._data:
                raise KeyError(f"已存在: {entry.name}")
            self._data[entry.name] = entry

    # ---- Read ----
    def read(self, name: str) -> ConfigEntry:
        with self._lock:
            try:
                return self._data[name]
            except KeyError:
                raise KeyError(f"未注册: {name}") from None

    def has(self, name: str) -> bool:
        return name in self._data

    def all(self) -> Tuple[ConfigEntry, ...]:
        with self._lock:
            return tuple(self._data.values())

    # ---- Update ----
    def update(self, entry: ConfigEntry) -> None:
        with self._lock:
            if entry.name not in self._data:
                raise KeyError(f"未注册: {entry.name}")
            self._data[entry.name] = entry

    # ---- Delete ----
    def delete(self, name: str) -> None:
        with self._lock:
            if name not in self._data:
                raise KeyError(f"未注册: {name}")
            del self._data[name]

    def clear(self) -> int:
        """
        清空所有条目，返回清除数量。
        供 hub.reload() 使用（保留锁对象本身，不换实例）。
        """
        with self._lock:
            n = len(self._data)
            self._data.clear()
            return n

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[ConfigEntry]:
        return iter(self.all())
