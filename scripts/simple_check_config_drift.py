"""check_config_drift — CI 守门：配置必须"单一真相源"。

两条断言：
  1. **name 全局唯一**：schema.yaml + 所有 section define() 里，同一个
     name 只允许出现一次。store 以 name 为键（不带 section），
     register_into 是"先到先得、后者静默跳过"——重复声明必然有一份是死配置。
  2. **secret 必须 from_env**：secret=true 但没声明 from_env=true 时，
     YAML 里的占位默认值会被当成真实密钥使用。

用法（项目根）：python scripts/check_config_drift.py
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_planner.infrastructure.cog.registry import get_registered_sections  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    names: collections.Counter[str] = collections.Counter()
    doc = yaml.safe_load((ROOT / "config" / "schema.yaml").read_text(encoding="utf-8")) or {}
    problems: list[str] = []

    for section, items in doc.items():
        if not isinstance(items, dict):
            continue
        for name, spec in items.items():
            if not isinstance(spec, dict):
                continue
            names[name] += 1
            if spec.get("secret") and not spec.get("from_env"):
                problems.append(f"{section}.{name}: secret=true 必须同时 from_env=true")

    for cls in get_registered_sections():
        for e in cls().define():
            names[e.name] += 1

    dup = {n: c for n, c in names.items() if c > 1}
    if dup:
        problems.append(f"配置项重复声明（store 先到先得，会静默丢值）: {dup}")

    if problems:
        for p in problems:
            print(f"❌ {p}", file=sys.stderr)
        return 1
    print(f"✅ 配置漂移检查通过（{sum(names.values())} 条，name 全局唯一）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
