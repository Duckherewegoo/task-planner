#!/usr/bin/env python3
"""
audit_private_imports.py — 跨模块私有名审计（v3）
═══════════════════════════════════════════════════════════════════════
检查两类问题：
  【A】__all__ 里包含 "_" 开头的名字（错误地公开私有）
  【B】跨模块 `from X import _private`（引用别人的私有）

v3 Changelog:
  ✅ P1-1：find_py_files 改用 relative_to(root) 过滤，
           避免用户主目录含 env/build 等关键词时误排整个项目。
  ✅ P2-1：相对导入解析加注释，说明为何不用 importlib.util.resolve_name。

设计说明：
  本脚本**纯静态分析**——不导入任何目标模块，只读 AST。
  因此不能依赖 importlib 的运行时机制（__package__ 需要模块已加载），
  相对导入解析必须自己实现。这不是"重复造轮子"，是场景要求。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple


# ═══════════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════════
class Finding(NamedTuple):
    kind: str
    file: str
    lineno: int
    target_module: str
    name: str
    alias: Optional[str]


class ModuleInfo(NamedTuple):
    name: str
    file: Path
    is_package: bool
    tree: ast.Module
    all_names: Set[str]


# ═══════════════════════════════════════════════════════════════════
#  路径工具
# ═══════════════════════════════════════════════════════════════════
_SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env",
    ".idea", ".vscode", "node_modules", "abandon",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs",
    ".compat_removal_backup", "task_planner.egg-info",
}


def find_project_root(start: Path) -> Path:
    for p in [start] + list(start.parents):
        if (p / "pyproject.toml").exists():
            return p
    return start


def find_py_files(root: Path) -> List[Path]:
    """✅ P1-1：用相对路径过滤，避免绝对路径误伤"""
    root = root.resolve()
    result: List[Path] = []
    for p in root.rglob("*.py"):
        try:
            rel_parts = p.relative_to(root).parts
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        result.append(p)
    return sorted(result)


def module_path_of(file: Path, src_root: Path) -> Optional[Tuple[str, bool]]:
    try:
        rel = file.relative_to(src_root)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    is_package = False
    if parts and parts[-1] == "__init__":
        parts.pop()
        is_package = True
    if not parts:
        return None
    return ".".join(parts), is_package


# ═══════════════════════════════════════════════════════════════════
#  名字判定
# ═══════════════════════════════════════════════════════════════════
def is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def is_private(name: str) -> bool:
    return name.startswith("_") and not is_dunder(name)


def is_task_planner_module(target: str) -> bool:
    return target == "task_planner" or target.startswith("task_planner.")


# ═══════════════════════════════════════════════════════════════════
#  相对导入解析
# ═══════════════════════════════════════════════════════════════════
#
#  为什么不用 importlib.util.resolve_name？
#    - 它接收 (name, package)，其中 package 是模块的 __package__
#    - __package__ 只有在模块被实际导入后才存在
#    - 本脚本**不导入**目标模块（静态分析），因此无法用它
#    - 手写版本 15 行，等价于 CPython _bootstrap._resolve_name
#
# ═══════════════════════════════════════════════════════════════════

def package_of(module_name: str, is_package: bool) -> str:
    """推 __package__：包用自身名，模块用父级"""
    if is_package:
        return module_name
    if "." in module_name:
        return module_name.rsplit(".", 1)[0]
    return ""


def resolve_relative_import(
    current_module: str,
    current_is_package: bool,
    level: int,
    module: Optional[str],
) -> Optional[str]:
    """相对导入 → 绝对模块名；超出顶层包返回 None"""
    if level == 0:
        return module
    pkg = package_of(current_module, current_is_package)
    if not pkg:
        return None
    dot = len(pkg)
    for _ in range(level, 1, -1):
        dot = pkg.rfind(".", 0, dot)
        if dot == -1:
            return None
    base = pkg[:dot]
    if module:
        return f"{base}.{module}" if base else module
    return base


# ═══════════════════════════════════════════════════════════════════
#  AST 收集
# ═══════════════════════════════════════════════════════════════════
def collect_dunder_all(tree: ast.Module) -> Set[str]:
    """收集 __all__（支持 Assign 和 AnnAssign 两种写法）"""
    names: Set[str] = set()

    def _extract(value: ast.AST) -> None:
        if isinstance(value, (ast.List, ast.Tuple)):
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "__all__"
                   for t in node.targets):
                _extract(node.value)
        elif isinstance(node, ast.AnnAssign):
            if (isinstance(node.target, ast.Name)
                    and node.target.id == "__all__"
                    and node.value is not None):
                _extract(node.value)
    return names


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description="跨模块私有名审计",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--also-tests", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    project_root = find_project_root(here)
    src_root = (args.src or (project_root / "src")).resolve()
    tests_root = (project_root / "tests").resolve()

    if not src_root.exists():
        print(f"[ERR] 源码根不存在: {src_root}", file=sys.stderr)
        print(f"      项目根: {project_root}", file=sys.stderr)
        return 2

    # ── 收集 src/ ──
    modules: Dict[str, ModuleInfo] = {}
    for path in find_py_files(src_root):
        info = module_path_of(path, src_root)
        if info is None:
            continue
        mod_name, is_package = info
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as e:
            print(f"[WARN] 跳过 {path}: {e}", file=sys.stderr)
            continue
        modules[mod_name] = ModuleInfo(
            name=mod_name, file=path, is_package=is_package,
            tree=tree, all_names=collect_dunder_all(tree),
        )

    # ── 收集 tests/（仅 A 类用）──
    test_all: List[Tuple[Path, Set[str]]] = []
    if args.also_tests and tests_root.exists():
        for path in find_py_files(tests_root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as e:
                print(f"[WARN] 跳过 {path}: {e}", file=sys.stderr)
                continue
            test_all.append((path, collect_dunder_all(tree)))

    # ── A 类 ──
    a_findings: List[Finding] = []
    for mod_name, m in modules.items():
        for name in m.all_names:
            if is_private(name):
                a_findings.append(Finding(
                    "all_private", str(m.file), 0, mod_name, name, None,
                ))
    for path, all_names in test_all:
        for name in all_names:
            if is_private(name):
                a_findings.append(Finding(
                    "all_private", str(path), 0, f"tests::{path.name}", name, None,
                ))

    # ── B 类 ──
    b_findings: List[Finding] = []
    for mod_name, m in modules.items():
        for node in ast.walk(m.tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level > 0:
                target = resolve_relative_import(
                    mod_name, m.is_package, node.level, node.module,
                )
            else:
                target = node.module
            if not target or not is_task_planner_module(target):
                continue
            for alias in node.names:
                if alias.name == "*" or not is_private(alias.name):
                    continue
                b_findings.append(Finding(
                    "cross_private", str(m.file), node.lineno,
                    target, alias.name, alias.asname,
                ))

    # ── 报告 ──
    sep = "═" * 78
    print(f"\n{sep}")
    print(f"私有名审计报告  (src={src_root}"
          + ("，含 tests" if args.also_tests else "") + ")")
    print(sep)

    print(f"\n【A】__all__ 里包含私有名 —— {len(a_findings)} 处")
    print("─" * 78)
    if a_findings:
        for f in a_findings:
            print(f"    {f.target_module}::{f.name}")
            print(f"      {f.file}")
    else:
        print("    ✅ 无")

    print(f"\n【B】跨模块引用私有名 —— {len(b_findings)} 处")
    print("─" * 78)
    if b_findings:
        by_target: Dict[str, List[Finding]] = {}
        for f in b_findings:
            by_target.setdefault(f.target_module, []).append(f)
        for target in sorted(by_target):
            items = by_target[target]
            mark = "✓" if target in modules else "?"
            print(f"  📦 [{mark}] {target}  ({len(items)} 处)")
            for f in items:
                alias_part = f" as {f.alias}" if f.alias else ""
                rel = Path(f.file)
                try:
                    rel = rel.relative_to(project_root)
                except ValueError:
                    pass
                print(f"      {rel}:{f.lineno}")
                print(f"        from {target} import {f.name}{alias_part}")
        print()
        print("  提示：'?' 表示解析出的模块未在 src 里找到")
    else:
        print("    ✅ 无")

    total = len(a_findings) + len(b_findings)
    print(f"\n{sep}")
    if total == 0:
        print("✅ 全部通过")
    else:
        print(f"⚠️  {total} 处待审查（A={len(a_findings)}, B={len(b_findings)}）")
    print(sep)

    if args.strict and total > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
