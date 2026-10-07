"""
writer.py — 配置写回（原子操作 + 保留注释优先）
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import (
    Any,
)

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.config_ui.writer")

# 优先用 ruamel.yaml（保留注释），没装就用标准库
try:
    from ruamel.yaml import YAML
    _HAS_RUAMEL = True
except ImportError:
    _HAS_RUAMEL = False


def _atomic_write(path: Path, content: str) -> None:
    """原子写：写临时文件再 rename，避免中途崩溃留下半截文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_user_yaml(path: Path, updates: dict[str, Any]) -> None:
    """
    写入 user.yaml。
    - 有 ruamel.yaml：合并更新，保留原注释
    - 没有：直接 dump，顶部加"工具管理"注释
    """
    if _HAS_RUAMEL:
        y = YAML()
        y.preserve_quotes = True
        data = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = y.load(f) or {}
        data.update(updates)
        with path.open("w", encoding="utf-8") as f:
            y.dump(data, f)
    else:
        import yaml
        header = (
            "# ═══════════════════════════════════════════════════════\n"
            "# user.yaml — 用户配置覆盖\n"
            "# ⚠️ 此文件由 task-planner-config 管理；手工编辑注释可能丢失\n"
            "# ═══════════════════════════════════════════════════════\n\n"
        )
        body = yaml.safe_dump(updates, allow_unicode=True, sort_keys=False)
        _atomic_write(path, header + body)
        return

    logger.info("[ConfigUI] ✅ 已写入 %s (%d 项)", path, len(updates))


def read_env(path: Path) -> dict[str, str]:
    """解析 .env 为 dict（忽略注释和空行）"""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def write_env(path: Path, updates: dict[str, str]) -> None:
    """
    更新 .env 指定 key。
    保留原文件行序、注释、空行；新 key 追加到末尾。
    """
    lines: list[str] = []
    seen: set[str] = set()

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    lines.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            lines.append(line)

    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}")

    _atomic_write(path, "\n".join(lines) + "\n")
    logger.info("[ConfigUI] ✅ 已写入 %s (%d 项)", path, len(updates))
