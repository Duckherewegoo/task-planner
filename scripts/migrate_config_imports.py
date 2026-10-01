#!/usr/bin/env python3
"""
migrate_config_imports.py — 把旧 config 引用全量迁移到 cog 新体系
═══════════════════════════════════════════════════════════════════════
做的事：
  1. 递归扫描目录下所有 .py
  2. 找 `from [task_planner.infrastructure.]config import ...`（单/多行通吃）
  3. 按变量分类重写成：
       from task_planner.infrastructure.cog import hub as _hub     ← 运行时
       from task_planner.infrastructure.constants import (...)     ← 常量
       from task_planner.infrastructure.regexes import (...)       ← 正则
       from task_planner.infrastructure.prompts.loader import (...)← Prompt
       from task_planner.infrastructure.ui_styles import (...)     ← 样式
       from task_planner.infrastructure.assets.cytoscape_styles import (...)
       from task_planner.infrastructure.assets.cytoscape_js import (...)
  4. 业务代码里对"运行时变量"的引用全部加 `_hub.dev.` 前缀
     （常量/正则/Prompt/样式/资源的名字保持原样，因为它们还是原名 import）

用法：
    # 先看 diff
    python scripts/migrate_config_imports.py --root src --dry-run

    # 真改
    python scripts/migrate_config_imports.py --root src --apply

    # 改动前备份 .bak
    python scripts/migrate_config_imports.py --root src --apply --backup

退出码：0=成功 / 1=有警告 / 2=参数错误
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Set


# ═══════════════════════════════════════════════════════════════════════
#  1. 变量分类表
# ═══════════════════════════════════════════════════════════════════════
RUNTIME_VARS: Set[str] = {
    # base
    "DEBUG", "LOG_DIR", "LOG_FILE", "LOG_MAX_DAYS", "LOG_CONSOLE_LEVEL",
    "DASH_DISABLE_VERSION_CHECK",
    # dashscope
    "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_REGION", "USE_MOCK_LLM",
    # llm
    "LLM_INTENT_MODEL", "LLM_PLANNER_MODEL", "LLM_NODE_MODEL",
    "LLM_INTENT_ENABLE_THINKING", "LLM_PLANNER_ENABLE_THINKING", "LLM_NODE_ENABLE_THINKING",
    "LLM_THINKING_BUDGET",
    "LLM_TIMEOUT", "LLM_MAX_RETRIES", "LLM_RETRY_BACKOFF", "LLM_NODE_TIMEOUT",
    "LLM_TASK_TOTAL_TIMEOUT", "MAX_TASK_RETRIES", "LATENCY_ACCEPTANCE_THRESHOLD",
    # mongo
    "MONGO_HOST", "MONGO_PORT", "MONGO_DB",
    # web
    "GRADIO_HOST", "GRADIO_PORT", "ENABLE_MCP", "DASH_HOST", "DASH_PORT",
    # render
    "RENDER_DPI", "RENDER_FONT", "RENDER_DIR", "RENDER_WIDTH", "RENDER_HEIGHT",
    "RENDER_EDGE_WIDTH", "RENDER_EDGE_LABEL_FONT_SIZE", "RENDER_SHOW_EDGE_LABELS",
    "FLOWCHART_CYCLE_DETECTION", "DEFAULT_EXPORT_FORMAT",
    # runtime
    "LLM_MAX_CONCURRENT", "LLM_CONNECTION_POOL_SIZE",
    # security
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

_ALL_KNOWN = (
    RUNTIME_VARS | CONSTANT_VARS | REGEX_VARS | PROMPT_VARS
    | UI_STYLE_VARS | ASSET_STYLE_VARS | ASSET_JS_VARS
)

# 有意忽略（旧 config 里删除的反模式）
IGNORE_NAMES = {"PULSE_CSS", "PROGRESS_CSS", "WERKZEUG_RUN_MAIN"}


# ═══════════════════════════════════════════════════════════════════════
#  2. 匹配旧 import 语句
# ═══════════════════════════════════════════════════════════════════════
_OLD_IMPORT_RE = re.compile(
    r"from\s+"
    r"(?:task_planner\.infrastructure\.config|\.config)"
    r"\s+import\s+"
    r"(?:\(([^)]*)\)|([^\n(]+))",
    re.MULTILINE,
)


def _parse_names(inner: str) -> Set[str]:
    """从 'A, B as C, D' 里提取 {A, C, D}"""
    inner = re.sub(r"#.*", "", inner)
    names: Set[str] = set()
    for part in inner.split(","):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            part = part.split(" as ", 1)[1].strip()
        if part.isidentifier():
            names.add(part)
    return names


# ═══════════════════════════════════════════════════════════════════════
#  3. 生成新 import 块
# ═══════════════════════════════════════════════════════════════════════
def _block(module: str, names: Set[str]) -> str:
    if not names:
        return ""
    lines = [f"from {module} import ("]
    for n in sorted(names):
        lines.append(f"    {n},")
    lines.append(")")
    return "\n".join(lines)


def build_new_imports(names: Set[str]) -> str:
    parts = []

    if names & RUNTIME_VARS:
        parts.append("from task_planner.infrastructure.cog import hub as _hub")

    parts.append(_block("task_planner.infrastructure.constants",
                        names & CONSTANT_VARS))
    parts.append(_block("task_planner.infrastructure.regexes",
                        names & REGEX_VARS))
    parts.append(_block("task_planner.infrastructure.prompts.loader",
                        names & PROMPT_VARS))
    parts.append(_block("task_planner.infrastructure.ui_styles",
                        names & UI_STYLE_VARS))
    parts.append(_block("task_planner.infrastructure.assets.cytoscape_styles",
                        names & ASSET_STYLE_VARS))
    parts.append(_block("task_planner.infrastructure.assets.cytoscape_js",
                        names & ASSET_JS_VARS))

    return "\n".join(p for p in parts if p)


# ═══════════════════════════════════════════════════════════════════════
#  4. 业务代码里加 _hub.dev. 前缀
# ═══════════════════════════════════════════════════════════════════════
def rewrite_usages(source: str, runtime_names: Set[str]) -> str:
    """
    对每一行：
      - 跳过 import 行 / 纯注释行
      - 用词边界正则替换 `VAR` → `_hub.dev.VAR`
    前提：该行不含 'import'
    """
    if not runtime_names:
        return source

    # 预编译：负向断言防止 obj.VAR、_hub.dev.VAR、xVAR 被误伤
    patterns = [
        (re.compile(r"(?<![\w.])" + re.escape(n) + r"(?![\w])"), n)
        for n in sorted(runtime_names, key=len, reverse=True)
    ]

    out_lines = []
    for line in source.split("\n"):
        stripped = line.strip()
        # 跳过 import 语句
        if stripped.startswith("from ") or stripped.startswith("import "):
            out_lines.append(line)
            continue
        # 跳过整行注释
        if stripped.startswith("#"):
            out_lines.append(line)
            continue
        # 业务行：逐个替换
        for pat, name in patterns:
            line = pat.sub(f"_hub.dev.{name}", line)
        out_lines.append(line)

    return "\n".join(out_lines)


# ═══════════════════════════════════════════════════════════════════════
#  5. 处理单文件
# ═══════════════════════════════════════════════════════════════════════
def transform(source: str) -> tuple[str, Set[str], Set[str]]:
    """
    返回 (新源码, 提取到的旧名字集合, 未知名字集合)
    """
    collected: Set[str] = set()
    unknown: Set[str] = set()

    def replacer(m: re.Match) -> str:
        inner = m.group(1) or m.group(2)
        names = _parse_names(inner)
        collected.update(names)
        known = names & _ALL_KNOWN
        unknown.update(names - _ALL_KNOWN - IGNORE_NAMES)
        return build_new_imports(known)

    new_source = _OLD_IMPORT_RE.sub(replacer, source)

    runtime_used = collected & RUNTIME_VARS
    new_source = rewrite_usages(new_source, runtime_used)

    return new_source, collected, unknown


# ═══════════════════════════════════════════════════════════════════════
#  6. 递归扫描
# ═══════════════════════════════════════════════════════════════════════
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "env", ".idea", ".vscode",
              "node_modules", "abandon", ".mypy_cache", ".pytest_cache", "dist", "build"}


def find_py_files(root: Path) -> list[Path]:
    result = []
    for p in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        result.append(p)
    return sorted(result)


# ═══════════════════════════════════════════════════════════════════════
#  7. 简单 diff 打印
# ═══════════════════════════════════════════════════════════════════════
def show_diff(path: Path, old: str, new: str) -> None:
    if old == new:
        return
    old_lines = old.split("\n")
    new_lines = new.split("\n")
    print(f"\n── {path} ──")
    import difflib
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"{path} (old)",
        tofile=f"{path} (new)",
        lineterm="",
        n=2,
    )
    for line in diff:
        print(line)


# ═══════════════════════════════════════════════════════════════════════
#  8. main
# ═══════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="迁移旧 config 引用到 cog")
    ap.add_argument("--root", type=Path, default=Path("src"),
                    help="递归根目录（默认 src）")
    ap.add_argument("--apply", action="store_true",
                    help="真正写入（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅显示 diff（默认行为）")
    ap.add_argument("--backup", action="store_true",
                    help="写入前生成 .bak")
    args = ap.parse_args()

    if not args.root.exists():
        print(f"[ERR] 根目录不存在: {args.root}", file=sys.stderr)
        return 2

    files = find_py_files(args.root)
    print(f"扫描 {len(files)} 个 .py 文件……")

    changed_files = 0
    unknown_total: Set[str] = set()

    for path in files:
        try:
            old = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[WARN] 读取失败 {path}: {e}", file=sys.stderr)
            continue

        if "config import" not in old:
            continue

        new, collected, unknown = transform(old)
        if new == old:
            continue

        changed_files += 1
        unknown_total |= unknown

        if args.apply:
            if args.backup:
                path.with_suffix(path.suffix + ".bak").write_text(
                    old, encoding="utf-8"
                )
            path.write_text(new, encoding="utf-8")
            print(f"✅ {path}  (改了 {len(collected)} 个 import)")
        else:
            show_diff(path, old, new)

    print()
    print("═" * 72)
    if args.apply:
        print(f"✅ 已写入 {changed_files} 个文件")
    else:
        print(f"🔍 dry-run：{changed_files} 个文件会被改动（加 --apply 生效）")
    print("═" * 72)

    if unknown_total:
        print(f"\n⚠️  以下名字未在分类表里，已忽略（请手动确认）:")
        for n in sorted(unknown_total):
            print(f"    - {n}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
