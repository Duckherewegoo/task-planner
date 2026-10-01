#!/usr/bin/env python3
"""
migrate_config_imports.py — v2（tokenize + AST）
═══════════════════════════════════════════════════════════════════════
关键改进 vs v1：
  - 用 tokenize 精确识别 NAME token：字符串/注释/f-string 内容一律不动
  - 用 AST 处理 import 语句：保持缩进、支持 `X as Y` 别名
  - `__all__` 自动清理（移除已迁移的旧名字）
  - 支持 --mode=alias|direct（默认 alias，保留模块级绑定，兼容旧 import）

模式说明：
  alias  —— 在 import 位置下方生成 `NAME = _hub.dev.NAME`，业务代码一字不改
  direct —— 业务代码里所有 `NAME` 改成 `_hub.dev.NAME`，支持运行时热更新
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ═══════════════════════════════════════════════════════════════════════
#  分类表（同 v1，只补了 SESSION_TTL 别名场景）
# ═══════════════════════════════════════════════════════════════════════
RUNTIME_VARS: Set[str] = {
    "DEBUG", "LOG_DIR", "LOG_FILE", "LOG_MAX_DAYS", "LOG_CONSOLE_LEVEL",
    "DASH_DISABLE_VERSION_CHECK",
    "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_REGION", "USE_MOCK_LLM",
    "LLM_INTENT_MODEL", "LLM_PLANNER_MODEL", "LLM_NODE_MODEL",
    "LLM_INTENT_ENABLE_THINKING", "LLM_PLANNER_ENABLE_THINKING", "LLM_NODE_ENABLE_THINKING",
    "LLM_THINKING_BUDGET",
    "LLM_TIMEOUT", "LLM_MAX_RETRIES", "LLM_RETRY_BACKOFF", "LLM_NODE_TIMEOUT",
    "LLM_TASK_TOTAL_TIMEOUT", "MAX_TASK_RETRIES", "LATENCY_ACCEPTANCE_THRESHOLD",
    "MONGO_HOST", "MONGO_PORT", "MONGO_DB",
    "GRADIO_HOST", "GRADIO_PORT", "ENABLE_MCP", "DASH_HOST", "DASH_PORT",
    "RENDER_DPI", "RENDER_FONT", "RENDER_DIR", "RENDER_WIDTH", "RENDER_HEIGHT",
    "RENDER_EDGE_WIDTH", "RENDER_EDGE_LABEL_FONT_SIZE", "RENDER_SHOW_EDGE_LABELS",
    "FLOWCHART_CYCLE_DETECTION", "DEFAULT_EXPORT_FORMAT",
    "LLM_MAX_CONCURRENT", "LLM_CONNECTION_POOL_SIZE",
    "MAX_INPUT_LENGTH", "MAX_CACHE_INPUT_BYTES", "SESSION_TTL",
}

CONSTANT_VARS: Set[str] = {
    "TASK_STATUS",
    "EDGE_TYPE_HARD", "EDGE_TYPE_SOFT", "EDGE_TYPE_CONDITIONAL", "EDGE_TYPE_RETRY",
    "DEFAULT_EDGE_TYPE", "EDGE_TYPE_CSS", "EDGE_TYPE_COLOR", "EDGE_TYPE_STYLE",
    "VALID_EDGE_TYPES",
    "STATUS_TEXT", "STATUS_COLOR", "STATUS_BORDER", "STATUS_ICONS",
    "NODE_TEXT_COLORS", "NODE_COLORS", "NODE_STYLES",
    "NODE_STATUS_COLORS", "NODE_STATUS_BORDERS", "NODE_STATUS_ICONS",
    "NODE_STATUS_CODE_MAP", "NODE_OPERABLE_STATES", "NODE_ACTION_CONFIG",
    "EDGE_STYLES", "DEFAULT_EDGE_STYLE", "EDGE_DASH_MAP",
    "STATE_COLORS", "STATE_LABELS", "HINT_TEMPLATES",
    "VALID_INTENT_CATEGORIES", "VALID_INTENT_COMPLEXITIES",
    "MOCK_RESPONSE_PREFIX", "USER_VISIBLE_NODE_FIELDS",
    "HISTORY_LABEL_MAX_LEN", "VALID_PORT_RANGE",
    "SUPPORTED_EXPORT_FORMATS", "FONT_FACE",
}

REGEX_VARS: Set[str] = {
    "THINK_RE", "UNREPLACED_PATTERN", "SKIP_PLANNING_PATTERNS",
    "SENSITIVE_PATTERNS", "SENSITIVE_PATTERNS_CONFIG",
}
PROMPT_VARS: Set[str] = {
    "INTENT_PROMPT", "PLANNER_PROMPT", "NODE_REFINE_PROMPT", "EXECUTE_NODE_PROMPT",
}
UI_STYLE_VARS: Set[str] = {
    "BASE_BTN_STYLE", "BUTTON_STYLE_PRIMARY", "BUTTON_STYLE_DANGER", "BUTTON_STYLE_SECONDARY",
    "SECTION_HEADER_STYLE", "ZOOM_TOOLBAR_STYLE",
    "BAR_DONE", "BAR_ERROR", "BAR_LOADING",
    "EMPTY_DAG", "EMPTY_STATES", "EMPTY_BAR_STYLE", "EMPTY_BAR_MINI_STYLE",
    "MARKDOWN_PRE_STYLE", "HINT_BASE_STYLE", "HINT_STYLES",
}
ASSET_STYLE_VARS: Set[str] = {"CYTO_STYLESHEET"}
ASSET_JS_VARS: Set[str] = {
    "GRAPH_CONFIGS",
    "FIT_JS_TEMPLATE", "ZOOM_BTN_JS_TEMPLATE", "ZOOM_SLIDER_JS_TEMPLATE",
}
IGNORE_NAMES = {"PULSE_CSS", "PROGRESS_CSS", "WERKZEUG_RUN_MAIN"}

_ALL_KNOWN = (RUNTIME_VARS | CONSTANT_VARS | REGEX_VARS | PROMPT_VARS
              | UI_STYLE_VARS | ASSET_STYLE_VARS | ASSET_JS_VARS)


# ═══════════════════════════════════════════════════════════════════════
#  Step 1: 用 AST 找 import 语句
# ═══════════════════════════════════════════════════════════════════════
def _is_old_config_import(node: ast.ImportFrom) -> bool:
    mod = node.module or ""
    return mod.endswith("config") and (
        mod == "config" or "task_planner.infrastructure.config" in mod
        or mod.startswith(".")
    ) and "cog" not in mod


def find_imports(tree: ast.Module) -> List[ast.ImportFrom]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _is_old_config_import(node):
            out.append(node)
    return out


# ═══════════════════════════════════════════════════════════════════════
#  Step 2: 生成新的 import 块
# ═══════════════════════════════════════════════════════════════════════
def _indent_of(lines: List[str], lineno: int) -> str:
    line = lines[lineno - 1]
    return line[: len(line) - len(line.lstrip())]


def _block(module: str, names: List[str], indent: str) -> List[str]:
    if not names:
        return []
    if len(names) == 1:
        return [f"{indent}from {module} import {names[0]}"]
    out = [f"{indent}from {module} import ("]
    for n in names:
        out.append(f"{indent}    {n},")
    out.append(f"{indent})")
    return out


def build_import_block(
    names: Set[str],
    indent: str,
    aliases: Dict[str, str],   # real_name -> alias
) -> Tuple[List[str], Dict[str, str]]:
    """
    返回 (import 行列表, {alias_name: real_runtime_name})
    """
    lines: List[str] = []
    runtime_aliases: Dict[str, str] = {}

    def with_alias(name: str) -> str:
        return f"{name} as {aliases[name]}" if name in aliases else name

    if names & RUNTIME_VARS:
        lines.append(f"{indent}from task_planner.infrastructure.cog import hub as _hub")

    for module, pool in [
        ("task_planner.infrastructure.constants", CONSTANT_VARS),
        ("task_planner.infrastructure.regexes", REGEX_VARS),
        ("task_planner.infrastructure.prompts.loader", PROMPT_VARS),
        ("task_planner.infrastructure.ui_styles", UI_STYLE_VARS),
        ("task_planner.infrastructure.assets.cytoscape_styles", ASSET_STYLE_VARS),
        ("task_planner.infrastructure.assets.cytoscape_js", ASSET_JS_VARS),
    ]:
        hit = sorted(names & pool)
        if hit:
            lines.extend(_block(module, [with_alias(n) for n in hit], indent))

    return lines, runtime_aliases


# ═══════════════════════════════════════════════════════════════════════
#  Step 3: 用 tokenize 精确重写 NAME token
# ═══════════════════════════════════════════════════════════════════════
def rewrite_names(source: str, runtime_names: Set[str]) -> str:
    """
    只把 NAME token 且不在 import 行里的，加 `_hub.dev.` 前缀。
    STRING / COMMENT / f-string 内容完全不动。
    """
    if not runtime_names:
        return source

    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))

    # 先扫描出所有 import 行号（这些行不动）
    import_lines: Set[int] = set()
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string in ("import", "from"):
            import_lines.add(tok.start[0])

    # 用词边界正则做安全替换，只针对 runtime names
    pattern = re.compile(
        r"(?<![\w.])(" + "|".join(
            re.escape(n) for n in sorted(runtime_names, key=len, reverse=True)
        ) + r")(?![\w])"
    )

    # 按行拆，只改非 import 行、非纯注释行的 NAME 部分
    lines = source.split("\n")
    for i, line in enumerate(lines, 1):
        if i in import_lines:
            continue
        if line.lstrip().startswith("#"):
            continue
        # 逐 token 判断：先把行 tokenize，找 NAME token 的列
        try:
            line_tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
        except tokenize.TokenizeError:
            continue
        spans: List[Tuple[int, int]] = []  # (start_col, end_col)
        for tok in line_tokens:
            if tok.type == tokenize.NAME and tok.string in runtime_names:
                spans.append((tok.start[1], tok.end[1]))
        if not spans:
            continue
        # 从后往前替换，避免列偏移
        new_line = line
        for start_col, end_col in reversed(spans):
            name = new_line[start_col:end_col]
            new_line = new_line[:start_col] + f"_hub.dev.{name}" + new_line[end_col:]
        lines[i - 1] = new_line

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  Step 4: 清理 __all__ 中的旧名字
# ═══════════════════════════════════════════════════════════════════════
def clean_dunder_all(source: str, migrated: Set[str]) -> str:
    """把 __all__ = [...] 里被迁移的名字删掉"""
    tree = ast.parse(source)
    lines = source.split("\n")
    edits: List[Tuple[int, int, str]] = []

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__all__"):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue

        # 找保留的字符串
        kept: List[str] = []
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                if elt.value in migrated:
                    continue
                kept.append(f'    "{elt.value}",')
        # 重建整个赋值
        head = lines[node.lineno - 1][: node.col_offset]
        if isinstance(node.value, ast.List):
            new = [f"{head}__all__ = ["] + kept + [f"{head}]"]
        else:
            new = [f"{head}__all__ = ("] + kept + [f"{head})"]
        edits.append((node.lineno - 1, node.end_lineno, "\n".join(new)))

    for start, end, text in reversed(edits):
        lines[start:end] = text.split("\n")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  Step 5: 单文件转换
# ═══════════════════════════════════════════════════════════════════════
def transform(source: str, mode: str) -> Tuple[str, Dict, Set]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return source, {"_error": f"语法错误: {e}"}, set()

    imports = find_imports(tree)
    if not imports:
        return source, {}, set()

    lines = source.split("\n")
    unknown: Set[str] = set()

    # 收集所有被 import 的名字 + 别名
    all_names: Set[str] = set()
    alias_map: Dict[str, str] = {}   # real -> alias
    runtime_alias: Dict[str, str] = {}  # alias -> real

    import_edits: List[Tuple[int, int, str, str]] = []  # (start, end, indent, replacement)

    for node in imports:
        # 记录原位置和缩进
        start_idx = node.lineno - 1
        end_idx = node.end_lineno
        indent = _indent_of(lines, node.lineno)

        # 抽取名字
        names: Set[str] = set()
        for alias in node.names:
            real = alias.name
            names.add(real)
            if alias.asname:
                alias_map[real] = alias.asname
                if real in RUNTIME_VARS:
                    runtime_alias[alias.asname] = real

        all_names.update(names)
        unknown.update(names - _ALL_KNOWN - IGNORE_NAMES)

        # 生成新块
        known = names & _ALL_KNOWN
        new_lines, _ = build_import_block(known, indent, alias_map)

        # alias 模式：运行时变量保留为模块级名字
        if mode == "alias":
            for real in sorted(known & RUNTIME_VARS):
                target = alias_map.get(real, real)
                new_lines.append(f"{indent}{target} = _hub.dev.{real}")

        import_edits.append((start_idx, end_idx, indent, "\n".join(new_lines)))

    # 应用 import 替换（从后往前）
    for start, end, _, new_text in reversed(import_edits):
        lines[start:end] = new_text.split("\n")
    source = "\n".join(lines)

    # direct 模式：业务代码里改 NAME token
    if mode == "direct":
        # 用别名映射还原：业务代码里出现 _SESSION_TTL，实际是 SESSION_TTL
        runtime_to_rewrite = set(RUNTIME_VARS) | set(runtime_alias.keys())
        source = rewrite_names(source, runtime_to_rewrite)

    # 清理 __all__
    source = clean_dunder_all(source, all_names & _ALL_KNOWN)

    return source, {"names": sorted(all_names), "aliases": alias_map}, unknown


# ═══════════════════════════════════════════════════════════════════════
#  Step 6: 文件遍历 + CLI
# ═══════════════════════════════════════════════════════════════════════
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "env",
              ".idea", ".vscode", "node_modules", "abandon",
              ".mypy_cache", ".pytest_cache", "dist", "build"}


def find_py_files(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.parts)
    )


def show_diff(path: Path, old: str, new: str) -> None:
    if old == new:
        return
    import difflib
    print(f"\n── {path} ──")
    for line in difflib.unified_diff(
        old.split("\n"), new.split("\n"),
        fromfile=f"{path} (old)", tofile=f"{path} (new)",
        lineterm="", n=2,
    ):
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("src"))
    ap.add_argument("--mode", choices=["alias", "direct"], default="alias",
                    help="alias=保留模块级变量（推荐）；direct=全走 _hub.dev.XXX")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup", action="store_true")
    args = ap.parse_args()

    if not args.root.exists():
        print(f"[ERR] 不存在: {args.root}", file=sys.stderr)
        return 2

    files = find_py_files(args.root)
    print(f"扫描 {len(files)} 个 .py 文件 (mode={args.mode})……")

    changed = 0
    unknown_total: Set[str] = set()

    for path in files:
        try:
            old = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[WARN] {path}: {e}", file=sys.stderr)
            continue
        if "config import" not in old:
            continue

        new, info, unknown = transform(old, args.mode)
        if info.get("_error"):
            print(f"[ERR] {path}: {info['_error']}", file=sys.stderr)
            continue
        if new == old:
            continue

        changed += 1
        unknown_total |= unknown

        if args.apply:
            if args.backup:
                path.with_suffix(path.suffix + ".bak").write_text(old, encoding="utf-8")
            path.write_text(new, encoding="utf-8")
            print(f"✅ {path}")
        else:
            show_diff(path, old, new)

    print()
    print("═" * 72)
    if args.apply:
        print(f"✅ 已写入 {changed} 个文件")
    else:
        print(f"🔍 dry-run：{changed} 个文件会变（--apply 生效）")
    print("═" * 72)

    if unknown_total:
        print("\n⚠️  未分类名字（已忽略）:")
        for n in sorted(unknown_total):
            print(f"    - {n}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
