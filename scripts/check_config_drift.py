#!/usr/bin/env python3
"""
check_config_drift.py — 配置漂移检测
═══════════════════════════════════════════════════════════════════
对比旧的大宗 config.py 与拆分后各单一职责新模块，
一次性找出：
  ❌ 缺失   —— 旧有新无
  ⚠️  值漂移 —— 同名不同值
  ➕ 新增   —— 新有旧无（仅供参考）
  🚫 重复   —— 同名在多个新文件中定义

用法：
    # 自动递归发现新模块
    python scripts/check_config_drift.py \
        --old src/task_planner/infrastructure/config_old.py \
        --new-root src/task_planner/infrastructure

    # 显式列出 + 附带 cog 的 YAML
    python scripts/check_config_drift.py \
        --old old_config.py \
        --new src/task_planner/infrastructure/constants.py \
        --new src/task_planner/infrastructure/regexes.py \
        --cog-yaml config/schema.yaml

    # 严格模式（有漂移则退出码 1，可用于 CI）
    python scripts/check_config_drift.py --old old.py --new-root new/ --strict

退出码：
    0 = 无缺失无漂移 / 1 = 有问题 / 2 = 参数错误
"""
from __future__ import annotations

import argparse
import ast
import logging
import re
import sys
import types
from pathlib import Path
from string import Template
from typing import (
    Any,
    Dict,
    List,
    Set,
    Tuple,
)


# ═══════════════════════════════════════════════════════════════════
#  1. 忽略清单 —— 有意删除 / 内部辅助 / 标准库，不报警
# ═══════════════════════════════════════════════════════════════════
IGNORE_NAMES: Set[str] = {
    # Python 标准库 / 常见导入
    "os", "re", "sys", "logging", "warnings", "Path",
    "Any", "Callable", "Dict", "List", "Set", "FrozenSet", "Tuple",
    "Template", "load_dotenv", "annotations", "importlib",
    # 旧 config 内部辅助
    "_env", "_PROJECT_ROOT", "_dashscope_url", "logger",
    # 有意删除 / 反模式
    "PULSE_CSS", "PROGRESS_CSS", "WERKZEUG_RUN_MAIN",
}


# ═══════════════════════════════════════════════════════════════════
#  2. 从单个 .py 提取顶层名字 + 值
# ═══════════════════════════════════════════════════════════════════
def extract_names(path: Path) -> Tuple[Set[str], Dict[str, Any]]:
    """
    返回 (声明的名字集合, 成功执行后拿到的值字典)。

    - 先用 AST 拿到「声明了哪些名字」（即使 exec 失败也能工作）
    - 再尝试 exec 拿到「值」（失败就标记 <UNEXECUTED>）
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[ERR] 无法读取 {path}: {e}", file=sys.stderr)
        return set(), {}

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"[ERR] {path}: 语法错误: {e}", file=sys.stderr)
        return set(), {}

    # ── 阶段 1：AST 收集顶层声明 ──
    declared: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    declared.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                declared.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                declared.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                declared.add(alias.asname or alias.name)

    # ── 阶段 2：尽力 exec 拿真实值 ──
    ns: Dict[str, Any] = {
        "__file__": str(path),
        "__name__": f"_driftcheck_{path.stem}",
    }
    try:
        code = compile(source, str(path), "exec")
        exec(code, ns)
    except Exception as e:
        print(
            f"[WARN] {path}: 执行失败，值对比将跳过"
            f"（{type(e).__name__}: {e}）",
            file=sys.stderr,
        )

    values: Dict[str, Any] = {}
    for name in declared:
        values[name] = ns.get(name, "<UNEXECUTED>")
    return declared, values


# ═══════════════════════════════════════════════════════════════════
#  3. 值对比（类型感知）
# ═══════════════════════════════════════════════════════════════════
_SKIP_TYPES = (types.ModuleType, logging.Logger, logging.Handler, type)


def _val_eq(a: Any, b: Any) -> bool:
    if a == "<UNEXECUTED>" or b == "<UNEXECUTED>":
        return True  # 没法对比，不误报
    if isinstance(a, _SKIP_TYPES) or isinstance(b, _SKIP_TYPES):
        return True
    # Template（Prompt）
    if isinstance(a, Template) and isinstance(b, Template):
        return a.template == b.template
    # 正则
    if isinstance(a, re.Pattern) and isinstance(b, re.Pattern):
        return a.pattern == b.pattern and a.flags == b.flags
    # 可调用（函数/类）：按名字比
    if callable(a) and callable(b):
        return getattr(a, "__name__", None) == getattr(b, "__name__", None)
    # 字典：递归
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_val_eq(a[k], b[k]) for k in a)
    # 列表 / 元组
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_val_eq(x, y) for x, y in zip(a, b))
    try:
        return a == b
    except Exception:
        return False


def _short_repr(v: Any, limit: int = 80) -> str:
    if v == "<UNEXECUTED>":
        return "<UNEXECUTED>"
    try:
        r = repr(v)
    except Exception:
        return f"<{type(v).__name__}>"
    return r if len(r) <= limit else r[: limit - 3] + "..."


# ═══════════════════════════════════════════════════════════════════
#  4. YAML 名字提取（用于 cog）
# ═══════════════════════════════════════════════════════════════════
def extract_from_yaml(path: Path) -> Set[str]:
    try:
        import yaml
    except ImportError:
        print("[WARN] 未安装 pyyaml，跳过 --cog-yaml", file=sys.stderr)
        return set()

    if not path.exists():
        print(f"[WARN] YAML 不存在: {path}", file=sys.stderr)
        return set()

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[ERR] 解析 YAML 失败 {path}: {e}", file=sys.stderr)
        return set()

    names: Set[str] = set()
    for section, items in data.items():
        if isinstance(items, dict):
            for name in items.keys():
                names.add(name)
    return names


# ═══════════════════════════════════════════════════════════════════
#  5. 收集新模块
# ═══════════════════════════════════════════════════════════════════
def discover_modules(
    root: Path, exclude_names: Set[str] | None = None
) -> List[Path]:
    exclude_names = exclude_names or set()
    result: List[Path] = []
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        if p.name in exclude_names:
            continue
        if p.name.startswith("_") and p.name != "__init__.py":
            continue
        result.append(p)
    return result


def collect_new(
    module_paths: List[Path],
) -> Tuple[Dict[str, Tuple[Any, Path]], List[Tuple[str, Path, Path]]]:
    """
    返回:
        name -> (value, source_path)
        dups: [(name, first_path, second_path), ...]
    """
    names: Dict[str, Tuple[Any, Path]] = {}
    dups: List[Tuple[str, Path, Path]] = []
    for p in module_paths:
        declared, values = extract_names(p)
        for n in declared:
            if n in names:
                dups.append((n, names[n][1], p))
            names[n] = (values.get(n, "<UNEXECUTED>"), p)
    return names, dups


# ═══════════════════════════════════════════════════════════════════
#  6. 主流程
# ═══════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description="配置漂移检测：旧 config.py vs 拆分后的新模块",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--old", required=True, type=Path,
                    help="旧 config.py 路径")
    ap.add_argument("--new", action="append", default=[], type=Path,
                    help="新模块路径（可多次指定）")
    ap.add_argument("--new-root", type=Path, default=None,
                    help="新模块根目录（自动递归发现所有 .py）")
    ap.add_argument("--cog-yaml", type=Path, default=None,
                    help="可选的 cog schema.yaml，把 YAML 中配置项也计入新集合")
    ap.add_argument("--ignore-extra", action="append", default=[],
                    help="额外忽略的名字（可多次指定）")
    ap.add_argument("--strict", action="store_true",
                    help="有任何缺失/漂移时退出码=1（CI 用）")
    args = ap.parse_args()

    if not args.old.exists():
        print(f"[ERR] 旧 config 不存在: {args.old}", file=sys.stderr)
        return 2

    # ── 确定新模块列表 ──
    new_paths: List[Path] = list(args.new)
    if args.new_root:
        new_paths.extend(discover_modules(args.new_root))
    if not new_paths and not args.cog_yaml:
        print("[ERR] 至少指定 --new / --new-root / --cog-yaml 之一",
              file=sys.stderr)
        return 2

    # ── 收集 ──
    old_names, old_values = extract_names(args.old)
    new_names, dups = collect_new(new_paths)

    # cog YAML 补充
    if args.cog_yaml:
        yaml_names = extract_from_yaml(args.cog_yaml)
        for n in yaml_names:
            if n not in new_names:
                new_names[n] = ("<FROM_YAML>", args.cog_yaml)

    # ── 过滤忽略名单 ──
    ignore = IGNORE_NAMES | set(args.ignore_extra)
    old_names -= ignore
    for n in list(new_names.keys()):
        if n in ignore:
            del new_names[n]

    # ── 三类差异 ──
    new_keys = set(new_names.keys())
    missing = sorted(old_names - new_keys)

    drift: List[Tuple[str, Any, Any, Path]] = []
    for name in sorted(old_names & new_keys):
        ov = old_values.get(name, "<UNEXECUTED>")
        nv, np_ = new_names[name]
        if nv == "<FROM_YAML>":
            continue
        if not _val_eq(ov, nv):
            drift.append((name, ov, nv, np_))

    added = sorted(new_keys - old_names)

    # ── 报告 ──
    sep = "═" * 72
    print(sep)
    print(f"旧文件 : {args.old}")
    print(f"新模块 : {len(new_paths)} 个")
    for p in new_paths:
        print(f"          {p}")
    if args.cog_yaml:
        print(f"cog    : {args.cog_yaml}")
    print(sep)

    print()
    print(f"❌ 缺失（旧有新无）: {len(missing)}")
    if missing:
        for n in missing:
            print(f"    - {n}")
    else:
        print("    ✅ 全部对应，无缺失")

    print()
    print(f"⚠️  值漂移（同名不同值）: {len(drift)}")
    if drift:
        for n, ov, nv, np_ in drift:
            print(f"    ~ {n}")
            print(f"        旧: {_short_repr(ov)}")
            print(f"        新: {_short_repr(nv)}   ← {np_}")
    else:
        print("    ✅ 所有同名变量值一致")

    print()
    print(f"➕ 新增（新有旧无，仅供参考）: {len(added)}")
    if added:
        for n in added[:50]:
            v, p = new_names[n]
            print(f"    + {n}  [{p.name}]")
        if len(added) > 50:
            print(f"    ... 还有 {len(added) - 50} 个")

    print()
    print(f"🚫 重复定义（同名在多个新文件）: {len(dups)}")
    if dups:
        for n, p1, p2 in dups:
            print(f"    ! {n}: {p1.name} 和 {p2.name}")
    else:
        print("    ✅ 无重复")

    # ── 结论 ──
    issues = len(missing) + len(drift)
    print()
    print(sep)
    if issues == 0:
        print("✅ 无缺失、无漂移，迁移完整")
    else:
        print(f"❌ 发现 {len(missing)} 个缺失 + {len(drift)} 个值漂移，请处理")
    print(sep)

    if args.strict and issues > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
