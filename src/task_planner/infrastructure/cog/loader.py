"""
loader.py — 读 YAML schema + user 覆盖 → 构建 ConfigEntry → 灌进 store。

优先级（从高到低）：
  1. 环境变量（仅 from_env=true 的字段）
  2. user.yaml（仅 scope=user 的字段）
  3. schema.yaml 的 default
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .entry import ConfigEntry, Scope
from .store import ConfigStore

_TYPE_MAP = {"int": int, "str": str, "float": float, "bool": bool}


class ConfigLoader:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store

    def load(
        self, schema_path: Path, user_path: Path | None = None
    ) -> None:
        schema = self._read_yaml(schema_path)
        overrides = self._read_yaml(user_path) if user_path else {}

        for section, items in (schema or {}).items():
            if not isinstance(items, dict):
                continue
            for name, spec in items.items():
                entry = self._build_entry(section, name, spec, overrides)
                if not self._store.has(name):
                    self._store.create(entry)

    # ── 核心：构造单条 entry ──
    def _build_entry(
        self,
        section: str,
        name: str,
        spec: dict[str, Any],
        overrides: dict[str, Any],
    ) -> ConfigEntry:
        t = _TYPE_MAP[spec["type"]]
        scope = Scope(spec["scope"])
        default = spec["default"]
        secret = spec.get("secret", False)
        from_env = spec.get("from_env", False)
        env_name = spec.get("env", name)   # 允许自定义变量名

        # ── 优先级 1：环境变量 ──
        if from_env:
            raw = os.getenv(env_name)
            value = self._coerce(raw, t) if raw is not None else default
            return ConfigEntry(
                name=name, value=value, default=default,
                purpose=spec["purpose"], scope=scope, type=t,
                section=section, secret=secret,
                from_env=True, env_name=env_name,
            )

        # ── 优先级 2：user.yaml（仅 scope=user） ──
        if scope == Scope.USER and name in overrides:
            value = self._coerce(overrides[name], t)
        # ── 优先级 3：schema default ──
        else:
            value = default

        return ConfigEntry(
            name=name, value=value, default=default,
            purpose=spec["purpose"], scope=scope, type=t,
            section=section, secret=secret,
            from_env=False, env_name=env_name,
        )

    # ── 类型转换 ──
    @staticmethod
    def _coerce(raw: Any, t: type) -> Any:
        if raw is None:
            return None
        if isinstance(raw, t):
            return raw
        if t is bool:
            return str(raw).strip().lower() in ("true", "1", "yes", "on", "y")
        return t(raw)

    @staticmethod
    def _read_yaml(path: Path | None) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
