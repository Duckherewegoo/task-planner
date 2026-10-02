#!/usr/bin/env python3
"""
remove_compat_shells.py — 移除重构期的兼容薄壳与旧名 alias（安全版 v2）
═══════════════════════════════════════════════════════════════════════
安全机制：
  ✅ 阶段化：Plan → Validate → Backup → Apply → Verify
  ✅ AST 验证：所有改动后的源码必须先通过 ast.parse 才能落盘
  ✅ 时间戳备份：写到 .compat_removal_backup/<时间戳>/，不用 .bak
  ✅ 全有或全无：任一文件验证失败 → 全部回滚，不写一个文件
  ✅ 备份含 manifest.json：回滚时可精确还原
  ✅ 默认 dry-run：必须显式 --apply

用法：
    # 1. dry-run（默认，安全）
    python scripts/remove_compat_shells.py

    # 2. 真干（先备份）
    python scripts/remove_compat_shells.py --apply

    # 3. 回滚（如果 --apply 后发现问题）
    python scripts/remove_compat_shells.py --rollback .compat_removal_backup/<ts>

退出码：
    0 = 成功
    1 = 验证失败（未写入任何文件）
    2 = 参数错误
    3 = 已回滚（apply 时失败）
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import (
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)


# ═══════════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════════

# 模块路径重写
MODULE_REWRITES: Dict[str, str] = {
    "task_planner.core.database": "task_planner.core.db",
    "task_planner.infrastructure.llm_client": "task_planner.infrastructure.llm",
    "task_planner.services.stream_manager": "task_planner.services.stream",
    "task_planner.main.dash_app": "task_planner.main.ui",
}

# 私有名 alias 重写
# key   = (原模块, 原名)
# value = (新模块, 新名, 保持的新别名)
ALIAS_REWRITES: Dict[Tuple[str, str], Tuple[str, str, str]] = {
    ("task_planner.services.agent", "_is_graph_finished"):
        ("task_planner.services.agent.view", "is_graph_finished", "_is_graph_finished"),
    ("task_planner.services.agent", "_make_initial_state"):
        ("task_planner.services.agent.state", "make_initial_state", "_make_initial_state"),
    ("task_planner.infrastructure.llm_client", "_extract_tail_json"):
        ("task_planner.infrastructure.llm.json_utils", "extract_tail_json", "_extract_tail_json"),
    ("task_planner.infrastructure.llm_client", "_normalize_llm_output"):
        ("task_planner.infrastructure.llm.json_utils", "normalize_llm_output", "_normalize_llm_output"),
    ("task_planner.utils.cytoscape_adapter", "_get_edge_type"):
        ("task_planner.utils.cytoscape_adapter", "get_edge_type", "_get_edge_type"),
}

# 待删薄壳文件（相对项目根）
SHELL_FILES: List[str] = [
    "src/task_planner/core/database.py",
    "src/task_planner/infrastructure/llm_client.py",
    "src/task_planner/services/stream_manager.py",
    "src/task_planner/main/dash_app.py",
]

# 待删 alias 定义行（相对项目根 → 正则列表）
LINES_TO_REMOVE: Dict[str, List[re.Pattern]] = {
    "src/task_planner/services/agent/__init__.py": [
        re.compile(r"^\s*from\s+\.\s*state\s+import\s+make_initial_state\s+as\s+_make_initial_state\b"),
        re.compile(r"^\s*from\s+\.\s*view\s+import\s+is_graph_finished\s+as\s+_is_graph_finished\b"),
    ],
    "src/task_planner/utils/cytoscape_adapter.py": [
        re.compile(r"^\s*_get_edge_type\s*=\s*get_edge_type\s*$"),
    ],
}

# 与待删行相邻的注释，也一起删
CONTEXT_COMMENT_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\s*#\s*向后兼容"),
    re.compile(r"^\s*#\s*═{4,}\s*$"),
    re.compile(r"^\s*#\s*─{4,}\s*$"),
]

# 跳过目录
_SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env",
    ".idea", ".vscode", "node_modules", "abandon",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs",
}


# ═══════════════════════════════════════════════════════════════════
#  基础工具
# ═══════════════════════════════════════════════════════════════════

def find_py_files(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.parts)
    )


def _extract_indent(s: str) -> str:
    for i, ch in enumerate(s):
        if ch not in " \t":
            return s[:i]
    return s


def _is_valid_identifier(name: str) -> bool:
    return name.isidentifier()


# ═══════════════════════════════════════════════════════════════════
#  Import 语句定位（手写解析器，不依赖脆弱正则）
# ═══════════════════════════════════════════════════════════════════

_IMPORT_HEAD_RE = re.compile(
    r"^([ \t]*)from\s+([\w.]+)\s+import\b",
    re.MULTILINE,
)


def _iter_imports(source: str) -> List[Tuple[int, int, str]]:
    """
    遍历所有 "from X import ..." 语句。
    返回 [(start, end, statement_text), ...]
    支持：
      - 单行：from x import a, b, c
      - 带尾注释：from x import a  # comment
      - 多行括号：from x import (\n  a,\n  b,\n)
    """
    result: List[Tuple[int, int, str]] = []
    for m in _IMPORT_HEAD_RE.finditer(source):
        start = m.start()
        pos = m.end()
        # 跳空白
        while pos < len(source) and source[pos] in " \t":
            pos += 1

        if pos >= len(source):
            continue

        if source[pos] == "(":
            # 多行带括号
            depth = 1
            pos += 1
            while pos < len(source) and depth > 0:
                c = source[pos]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                elif c == "#":
                    # 跳过注释到行尾
                    while pos < len(source) and source[pos] != "\n":
                        pos += 1
                    continue
                pos += 1
            end = pos
        else:
            # 单行
            line_end = source.find("\n", pos)
            if line_end == -1:
                line_end = len(source)
            hash_pos = source.find("#", pos, line_end)
            end = hash_pos if hash_pos != -1 else line_end
            # 回退尾随空白
            while end > pos and source[end - 1] in " \t":
                end -= 1

        result.append((start, end, source[start:end]))
    return result


def _parse_import_items(stmt: str) -> Tuple[str, List[Tuple[str, Optional[str]]]]:
    """
    解析 import 语句。
    Returns (module, [(name, alias_or_None), ...])
    """
    m = _IMPORT_HEAD_RE.match(stmt)
    if not m:
        raise ValueError(f"Not an import statement: {stmt!r}")
    module = m.group(2)
    # 剥离头部
    body = stmt[m.end():].strip()
    # 剥离括号
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    # 剥离注释
    body = re.sub(r"#[^\n]*", "", body)
    # 按逗号拆分
    items: List[Tuple[str, Optional[str]]] = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            name, alias = (x.strip() for x in part.split(" as ", 1))
            if _is_valid_identifier(name) and _is_valid_identifier(alias):
                items.append((name, alias))
        else:
            if _is_valid_identifier(part):
                items.append((part, None))
    return module, items


def _format_import(
    module: str,
    items: List[Tuple[str, Optional[str]]],
    indent: str = "",
) -> str:
    if not items:
        return ""
    if len(items) == 1:
        name, alias = items[0]
        line = f"from {module} import {name}" + (f" as {alias}" if alias else "")
        return indent + line
    lines = [f"{indent}from {module} import ("]
    for name, alias in items:
        entry = f"{name} as {alias}" if alias else name
        lines.append(f"{indent}    {entry},")
    lines.append(f"{indent})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  改写逻辑
# ═══════════════════════════════════════════════════════════════════

def rewrite_source(source: str) -> Tuple[str, int]:
    """改写源码 → (新源码, 改动数)"""
    edits: List[Tuple[int, int, str]] = []  # (start, end, new_text)

    for start, end, stmt in _iter_imports(source):
        try:
            module, items = _parse_import_items(stmt)
        except ValueError:
            continue

        indent = _extract_indent(source[start:end])

        stay: List[Tuple[str, Optional[str]]] = []
        move: Dict[str, List[Tuple[str, Optional[str]]]] = {}

        for name, alias in items:
            key = (module, name)
            if key in ALIAS_REWRITES:
                new_mod, new_name, keep_alias = ALIAS_REWRITES[key]
                # 如果原语句有别名，保留原别名；否则用规则里的 keep_alias
                final_alias = alias if alias else keep_alias
                move.setdefault(new_mod, []).append((new_name, final_alias))
            else:
                stay.append((name, alias))

        new_module = MODULE_REWRITES.get(module, module)

        parts: List[str] = []
        if stay:
            parts.append(_format_import(new_module, stay, indent))
        for new_mod, new_items in move.items():
            parts.append(_format_import(new_mod, new_items, indent))

        new_text = "\n".join(p for p in parts if p)
        if new_text != stmt:
            edits.append((start, end, new_text))

    # 从后往前替换，避免偏移
    for start, end, new_text in reversed(edits):
        source = source[:start] + new_text + source[end:]

    return source, len(edits)


def remove_alias_lines(source: str, patterns: List[re.Pattern]) -> Tuple[str, int]:
    """删除匹配行 + 相关注释"""
    lines = source.split("\n")
    to_delete: Set[int] = set()

    for i, line in enumerate(lines):
        if any(p.match(line) for p in patterns):
            to_delete.add(i)

    # 向上扩展：连续的注释或空行一起删
    extra: Set[int] = set()
    for i in list(to_delete):
        j = i - 1
        while j >= 0:
            stripped = lines[j].strip()
            if not stripped:
                extra.add(j)
                j -= 1
                continue
            if any(p.match(lines[j]) for p in CONTEXT_COMMENT_PATTERNS):
                extra.add(j)
                j -= 1
                continue
            break
    to_delete |= extra

    if not to_delete:
        return source, 0

    new_lines = [l for i, l in enumerate(lines) if i not in to_delete]
    return "\n".join(new_lines), len(to_delete)


# ═══════════════════════════════════════════════════════════════════
#  验证
# ═══════════════════════════════════════════════════════════════════

def validate_python(path: Path, source: str) -> Optional[str]:
    """AST 验证；返回 None 表示通过，返回字符串表示错误信息"""
    try:
        ast.parse(source, filename=str(path))
        return None
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}: {e.msg}"


# ═══════════════════════════════════════════════════════════════════
#  Diff 输出
# ═══════════════════════════════════════════════════════════════════

def show_diff(path: Path, old: str, new: str, context: int = 2) -> None:
    if old == new:
        return
    print(f"\n── {path} ──")
    for line in difflib.unified_diff(
        old.split("\n"), new.split("\n"),
        fromfile=f"{path} (old)", tofile=f"{path} (new)",
        lineterm="", n=context,
    ):
        print(line)


# ═══════════════════════════════════════════════════════════════════
#  备份
# ═══════════════════════════════════════════════════════════════════

def make_backup_dir(root: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / ".compat_removal_backup" / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_file(path: Path, root: Path, backup_dir: Path) -> str:
    """复制文件到备份目录，返回相对 root 的路径（作为 manifest key）"""
    rel = str(path.relative_to(root))
    dst = backup_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
    return rel


def write_manifest(backup_dir: Path, info: dict) -> None:
    (backup_dir / "manifest.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════
#  回滚
# ═══════════════════════════════════════════════════════════════════

def do_rollback(backup_dir: Path, root: Path) -> int:
    if not backup_dir.exists():
        print(f"[ERR] 备份目录不存在: {backup_dir}", file=sys.stderr)
        return 2
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"[ERR] manifest 缺失: {manifest_path}", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    modified = manifest.get("modified", [])   # [(rel, ...), ...]
    deleted = manifest.get("deleted", [])     # [rel, ...]

    print(f"回滚 {len(modified)} 个文件，恢复 {len(deleted)} 个已删文件……")

    # 1. 恢复 modified
    for rel in modified:
        src = backup_dir / rel
        dst = root / rel
        if not src.exists():
            print(f"  [WARN] 备份缺 {rel}", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  ↩️  {rel}")

    # 2. 恢复 deleted
    for rel in deleted:
        src = backup_dir / rel
        dst = root / rel
        if not src.exists():
            print(f"  [WARN] 备份缺 {rel}", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  ↩️  {rel}")

    print(f"\n✅ 回滚完成。备份目录保留：{backup_dir}")
    return 0


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="移除重构期留下的兼容薄壳与旧名 alias（安全版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--apply", action="store_true",
                    help="真正写入（默认 dry-run，先 AST 验证 + 备份）")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅显示 diff（默认行为）")
    ap.add_argument("--rollback", type=Path, default=None,
                    help="从指定备份目录回滚")
    args = ap.parse_args()

    # ── 回滚模式 ──
    if args.rollback is not None:
        return do_rollback(args.rollback, args.root.resolve())

    if args.apply and args.dry_run:
        print("[ERR] --apply 与 --dry-run 不能同时使用", file=sys.stderr)
        return 2
    if not args.root.exists():
        print(f"[ERR] 根目录不存在: {args.root}", file=sys.stderr)
        return 2

    root = args.root.resolve()
    apply_mode = args.apply

    # ══════════════════════════════════════════════════════════
    # 阶段 1：Plan —— 计算所有改动
    # ══════════════════════════════════════════════════════════
    print("═" * 72)
    print(f"阶段 1/5：Plan（root={root}）")
    print("═" * 72)

    py_files = find_py_files(root)
    print(f"  扫描 {len(py_files)} 个 .py 文件")

    # 每个文件的改动：{绝对路径: (旧内容, 新内容, 改动数)}
    changes: Dict[Path, Tuple[str, str, int]] = {}

    for path in py_files:
        try:
            old = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  [WARN] 读取失败 {path}: {e}", file=sys.stderr)
            continue

        new, n_imports = rewrite_source(old)

        rel = str(path.relative_to(root))
        if rel in LINES_TO_REMOVE:
            new, n_lines = remove_alias_lines(new, LINES_TO_REMOVE[rel])
        else:
            n_lines = 0

        if new != old:
            changes[path] = (old, new, n_imports + n_lines)

    print(f"  计划改动 {len(changes)} 个文件")

    # ══════════════════════════════════════════════════════════
    # 阶段 2：Validate —— AST 验证
    # ══════════════════════════════════════════════════════════
    print()
    print("═" * 72)
    print("阶段 2/5：Validate（AST 语法检查）")
    print("═" * 72)

    errors: List[Tuple[Path, str]] = []
    for path, (_old, new, _n) in changes.items():
        err = validate_python(path, new)
        if err:
            errors.append((path, err))

    if errors:
        print(f"  ❌ {len(errors)} 个文件语法检查失败：")
        for path, err in errors:
            print(f"    {path}")
            print(f"      {err}")
        print()
        print("  🛑 已中止。未写入任何文件。")
        print("     修复脚本规则后重试。")
        return 1

    print(f"  ✅ 所有 {len(changes)} 个文件语法检查通过")

    # 待删文件检查
    shell_paths = [root / rel for rel in SHELL_FILES]
    existing_shells = [p for p in shell_paths if p.exists()]
    print(f"  ✅ 待删薄壳 {len(existing_shells)}/{len(SHELL_FILES)} 存在")

    # ══════════════════════════════════════════════════════════
    # 阶段 3：Preview / Backup
    # ══════════════════════════════════════════════════════════
    print()
    print("═" * 72)
    print(f"阶段 3/5：{'Backup' if apply_mode else 'Preview（dry-run）'}")
    print("═" * 72)

    if not apply_mode:
        # dry-run：显示 diff
        for path in sorted(changes.keys()):
            old, new, _ = changes[path]
            show_diff(path, old, new)

        print()
        print("─" * 72)
        print("待删文件：")
        print("─" * 72)
        for p in shell_paths:
            if p.exists():
                print(f"  🗑️  {p.relative_to(root)}")

        print()
        print("═" * 72)
        print(f"🔍 dry-run：{len(changes)} 文件待改，{len(existing_shells)} 薄壳待删")
        print("═" * 72)
        print()
        print("执行（会先备份）：")
        print("  python scripts/remove_compat_shells.py --apply")
        return 0

    # apply 模式：备份
    backup_dir = make_backup_dir(root)
    print(f"  备份目录: {backup_dir}")

    backed_up: List[str] = []
    for path in sorted(changes.keys()):
        rel = backup_file(path, root, backup_dir)
        backed_up.append(rel)

    backed_up_deleted: List[str] = []
    for p in shell_paths:
        if p.exists():
            rel = backup_file(p, root, backup_dir)
            backed_up_deleted.append(rel)

    write_manifest(backup_dir, {
        "timestamp": time.time(),
        "root": str(root),
        "modified": backed_up,
        "deleted": backed_up_deleted,
    })
    print(f"  ✅ 已备份 {len(backed_up)} 文件 + {len(backed_up_deleted)} 待删文件")

    # ══════════════════════════════════════════════════════════
    # 阶段 4：Apply
    # ══════════════════════════════════════════════════════════
    print()
    print("═" * 72)
    print("阶段 4/5：Apply")
    print("═" * 72)

    written: List[Path] = []
    deleted: List[Path] = []

    try:
        for path in sorted(changes.keys()):
            _old, new, n = changes[path]
            path.write_text(new, encoding="utf-8")
            written.append(path)
            print(f"  ✅ 改写 {path.relative_to(root)}  ({n} 处)")

        for p in shell_paths:
            if p.exists():
                p.unlink()
                deleted.append(p)
                print(f"  🗑️  删除 {p.relative_to(root)}")

    except Exception as e:
        print(f"\n  ❌ 写入过程中异常：{e}")
        print(f"  🔄 尝试自动回滚……")
        do_rollback(backup_dir, root)
        return 3

    # ══════════════════════════════════════════════════════════
    # 阶段 5：Verify —— 再验一次
    # ══════════════════════════════════════════════════════════
    print()
    print("═" * 72)
    print("阶段 5/5：Verify")
    print("═" * 72)

    verify_failed = False
    for path in written:
        try:
            content = path.read_text(encoding="utf-8")
            err = validate_python(path, content)
            if err:
                print(f"  ❌ {path.relative_to(root)}: {err}")
                verify_failed = True
        except OSError as e:
            print(f"  ❌ {path.relative_to(root)}: 读取失败 {e}")
            verify_failed = True

    if verify_failed:
        print()
        print("  🛑 验证失败！自动回滚……")
        do_rollback(backup_dir, root)
        return 3

    print(f"  ✅ {len(written)} 文件语法正确，{len(deleted)} 薄壳已删")

    # ══════════════════════════════════════════════════════════
    # 完成
    # ══════════════════════════════════════════════════════════
    print()
    print("═" * 72)
    print(f"✅ 全部完成")
    print("═" * 72)
    print()
    print("下一步：")
    print("  1. 跑测试:  pytest tests/ -q")
    print("  2. 全链路:  python -c 'import task_planner; from task_planner.main.ui import app; print(\"OK\")'")
    print("  3. 手工改 README.md 里 'main.dash_app' → 'main.ui.app'")
    print()
    print(f"如需回滚：")
    print(f"  python scripts/remove_compat_shells.py --rollback {backup_dir.relative_to(root)}")
    print()
    print(f"确认无误后删除备份：")
    print(f"  rm -rf {backup_dir.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
