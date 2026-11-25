"""
loader.py — 读 YAML schema + user 覆盖 → 构建 ConfigEntry → 灌进 store。

优先级（从高到低）：
  1. 环境变量（仅 from_env=true 的字段）
  2. user.yaml（仅 scope=user 的字段）
  3. schema.yaml 的 default

⚠️ 与代码内置 section 的关系：loader 先跑，section.register_into 后跑，
   store.has(name) 已存在即跳过——所以 **YAML 优先于代码内置项**。
   代码 section 是"YAML 没写的那部分"的兜底 + 类型声明，不是第二份真相源。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import yaml

from .entry import ConfigEntry, Scope
from .store import ConfigStore

_TYPE_MAP: dict[str, type] = {"int": int, "str": str, "float": float, "bool": bool}

# from_env 字段若没从环境变量拿到值，默认值必须是空——否则会出现
# "密钥用 YAML 里的空串/占位串连上了远端"这种难以察觉的错误。
_SECRET_PLACEHOLDERS = frozenset({"", "changeme", "your_api_key", "sk-xxx", "xxx"})


class ConfigLoader:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store

    def load(self, schema_path: Path, user_path: Path | None = None) -> None:
        schema = self._read_yaml(schema_path)
        overrides = self._read_yaml(user_path) if user_path else {}

        for section, raw_items in (schema or {}).items():
            if not isinstance(raw_items, dict):
                continue
            items = cast("dict[str, Any]", raw_items)
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
        if not isinstance(spec, dict):
            raise ValueError(f"{section}.{name}: 取值必须是映射，收到 {type(spec).__name__}")

        scope = Scope(spec["scope"])
        default = spec["default"]
        secret = bool(spec.get("secret", False))
        from_env = bool(spec.get("from_env", False))
        env_name = spec.get("env", name)  # 允许自定义变量名
        try:
            t = _TYPE_MAP[spec["type"]]
        except KeyError:
            raise ValueError(
                f"{section}.{name}: 未知类型 {spec['type']!r}，可选 {sorted(_TYPE_MAP)}"
            ) from None

        # ── 防御：密钥必须走环境变量，不许把默认值当真值用 ──
        if secret and not from_env:
            raise ValueError(
                f"{section}.{name}: secret=true 必须同时声明 from_env=true，"
                "否则默认值会被当成真实密钥使用"
            )

        # ── 优先级 1：环境变量 ──
        if from_env:
            raw = os.getenv(env_name)
            if raw is None:
                if secret or str(default).strip() in _SECRET_PLACEHOLDERS:
                    # 不落占位值：留空，让使用者拿到明确的"未配置"
                    value = "" if t is str else default
                else:
                    value = default
            else:
                value = self._coerce(raw, t)
            return ConfigEntry(
                name=name,
                value=value,
                default=default,
                purpose=spec["purpose"],
                scope=scope,
                type=t,
                section=section,
                secret=secret,
                from_env=True,
                env_name=env_name,
            )

        # ── 优先级 2：user.yaml（仅 scope=user） ──
        if scope is Scope.USER and name in overrides:
            value = self._coerce(overrides[name], t)
        # ── 优先级 3：schema default ──
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
            secret=secret,
            from_env=False,
            env_name=env_name,
        )

    # ── 类型转换 ──
    @staticmethod
    def _coerce(raw: Any, t: type) -> Any:
        if t is bool:
            return str(raw).strip().lower() in ("true", "1", "yes", "on", "y")
        if t is int:
            # bool 是 int 的子类，True 会被静默转成 1——显式拒绝
            if isinstance(raw, bool):
                raise ValueError(f"整数配置项收到布尔值 {raw!r}，请检查 YAML")
            if isinstance(raw, float) and not raw.is_integer():
                raise ValueError(f"整数配置项收到小数 {raw!r}")
            return int(raw)
        if isinstance(raw, t):
            return raw
        return t(raw)

    @staticmethod
    def _read_yaml(path: Path | None) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
