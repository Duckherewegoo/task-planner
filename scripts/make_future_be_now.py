#!/usr/bin/env python3
"""
strip_future_annotations.py — 批量删除 .py 文件里的
`from __future__ import annotations`。

设计原则：
  - 默认 dry-run，只有显式 --apply 才真删
  - 只删"整行就是这条 import"的情况，绝不动复合 import
  - 保留原文件换行风格、编码
  - 跳过常见虚拟环境 / 缓存 / 版本控制目录
  - 报告每个文件删了几行、跳过几行

用法：
    # 1) 先看会改哪些，不改文件
    python strip_future_annotations.py --root .

    # 2) 确认无误后真删
    python strip_future_annotations.py --root . --apply

    # 3) 自定义排除目录
    python strip_future_annotations.py --root . --exclude build --exclude dist

建议在 git 干净工作区下跑，随时 `git diff` / `git restore` 回滚。
"""
from __future__ import annotations  # noqa: F401  (脚本自身用，无妨)

import argparse
import re
import sys
from pathlib import Path

# 精确匹配"整行只有 from __future__ import annotations"
# 允许行首缩进、行尾空白；不允许行内有逗号（那是复合 import）
_STRICT_RE = re.compile(
    r"^[ \t]*from[ \t]+__future__[ \t]+import[ \t]+annotations[ \t]*$"
)

# 宽松匹配：仅用于"提醒用户手工检查"，不用于删除
_LOOSE_RE = re.compile(
    r"from[ \t]+__future__[ \t]+import[ \t]+.*\bannotations\b"
)

_DEFAULT_EXCLUDES = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "build", "dist", ".tox", ".eggs",
    "site-packages",
}


def iter_py_files(root: Path, excludes: set[str]):
    for path in root.rglob("*.py"):
        # 跳过任何路径段命中排除集合的文件
        if any(part in excludes for part in path.parts):
            continue
        yield path


def strip_file(path: Path, apply: bool) -> tuple[int, int]:
    """
    返回 (removed_count, loose_warn_count)。
    apply=False 时只看不改。
    """
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"  [skip] 非 UTF-8，未处理: {path}", file=sys.stderr)
        return 0, 0

    # 保留行尾：先按 splitlines(keepends=True) 拆
    lines = original.splitlines(keepends=True)

    kept: list[str] = []
    removed = 0
    for line in lines:
        stripped_of_eol = line.rstrip("\r\n")
        if _STRICT_RE.match(stripped_of_eol):
            removed += 1
            continue
        kept.append(line)

    # 顺便检测"复合 import 里含 annotations"的行，仅提示
    loose = sum(1 for ln in lines if _LOOSE_RE.search(ln) and not _STRICT_RE.match(ln.rstrip("\r\n")))

    if removed and apply:
        new_text = "".join(kept)
        path.write_text(new_text, encoding="utf-8")

    return removed, loose


def main() -> int:
    ap = argparse.ArgumentParser(description="删除 .py 里的 from __future__ import annotations")
    ap.add_argument("--root", default=".", help="扫描根目录，默认当前目录")
    ap.add_argument("--apply", action="store_true", help="真正写回文件；不加则只 dry-run")
    ap.add_argument(
        "--exclude", action="append", default=[],
        help="额外排除的目录名（可多次传入，例如 --exclude build）",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"root 不是目录: {root}", file=sys.stderr)
        return 2

    excludes = _DEFAULT_EXCLUDES | set(args.exclude)

    total_files = 0
    total_removed = 0
    total_loose = 0
    touched_files: list[tuple[Path, int]] = []

    for py in iter_py_files(root, excludes):
        total_files += 1
        removed, loose = strip_file(py, apply=args.apply)
        if removed:
            touched_files.append((py, removed))
            total_removed += removed
        total_loose += loose

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] root={root}")
    print(f"  扫描 .py 文件: {total_files}")
    print(f"  命中需删除的文件: {len(touched_files)}")
    print(f"  删除行总数: {total_removed}")

    if touched_files:
        print("  受影响文件（相对 root）:")
        for p, n in touched_files:
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = p
            print(f"    - {rel}  (删 {n} 行)")

    if total_loose:
        print()
        print(f"  ⚠️  发现 {total_loose} 处 'from __future__ import ...' 含 annotations 但不是单独一行")
        print("      （例如复合 import），脚本未处理，请手工检查：")
        # 二次扫描只为了打印位置
        for py in iter_py_files(root, excludes):
            try:
                text = py.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for i, ln in enumerate(text.splitlines(), 1):
                if _LOOSE_RE.search(ln) and not _STRICT_RE.match(ln):
                    try:
                        rel = py.relative_to(root)
                    except ValueError:
                        rel = py
                    print(f"    - {rel}:{i}  {ln.strip()}")

    if not args.apply and touched_files:
        print()
        print("  (dry-run 结束。加 --apply 才会写入文件。)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
