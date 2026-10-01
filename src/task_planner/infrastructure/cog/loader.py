"""
loader.py — 读 YAML schema + user 覆盖 → 构建 ConfigEntry → 灌进 store。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .entry import ConfigEntry, Scope
from .store import ConfigStore


_TYPE_MAP = {"int": int, "str": str, "float": float, "bool": bool}


class ConfigLoader:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store

    def load(self, schema_path: Path, user_path: Optional[Path] = None) -> None:
        schema = self._read_yaml(schema_path)
        overrides = self._read_yaml(user_path) if user_path else {}

        for section, items in (schema or {}).items():
            for name, spec in items.items():
                entry = self._build_entry(section, name, spec, overrides)
                if not self._store.has(name):
                    self._store.create(entry)

    def _build_entry(
        self, section: str, name: str, spec: Dict[str, Any], overrides: Dict[str, Any]
    ) -> ConfigEntry:
        t = _TYPE_MAP[spec["type"]]
        scope = Scope(spec["scope"])
        default = spec["default"]

        # ⚠️ 只有 scope=user 的项才受 user.yaml 影响，防越权
        if scope == Scope.USER and name in overrides:
            raw = overrides[name]
            value = self._coerce(raw, t)
        else:
            value = default

        return ConfigEntry(
            name=name,
            value=value,
            default=default,
            purpose=spec["purpose"],
            scope=scope,
            type=t,
            section=section,
            secret=spec.get("secret", False),
        )

    @staticmethod
    def _coerce(raw: Any, t: type) -> Any:
        if isinstance(raw, t):
            return raw
        if t is bool:
            return str(raw).strip().lower() in ("true", "1", "yes", "on", "y")
        return t(raw)

    @staticmethod
    def _read_yaml(path: Optional[Path]) -> Dict[str, Any]:
        if not path or not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
