#!/usr/bin/env python3
"""
changelog_migrate.py — 将模块顶部 docstring 里的旧版本 Changelog 迁至 CHANGELOG.md

设计目标：
  1. 兼容多种版本头格式（── v1 ── / ── v7.1 ── / ── v6.0（vs v5.0）── ...）
  2. 源文件不留垃圾——docstring 内空行压缩、`注释` 后空行压缩
  3. 统一指针——每个迁移过的文件结尾是 "⚠️ 完整变更历史见 CHANGELOG.md。"
  4. CHANGELOG.md 保留完整历史（含最新段）——md 是权威档案
  5. 幂等——已迁移过的文件跳过（检测指针存在）
  6. dry-run 默认；--apply 才写

用法：
  python scripts/changelog_migrate.py                   # dry-run
  python scripts/changelog_migrate.py --apply           # 写入（CHANGELOG.md 不存在时）
  python scripts/changelog_migrate.py --apply --force   # 覆盖已存在的 CHANGELOG.md
  python scripts/changelog_migrate.py --root <dir>      # 自定义扫描根
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ──────────────────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────────────────
POINTER = "⚠️ 完整变更历史见 CHANGELOG.md。"

# 排除清单——独立 drop-in 库，Changelog 保留在文件内更合理
EXCLUDE_PATHS: set[str] = {
    "src/task_planner/utils/good_addons.py",
}

# 版本头匹配——兼容多形态：
#   ── v1 ──
#   ── v7.1 ──
#   ── v6.0（vs v5.0）──
#   ── v2（KISS 加固，不影响功能）──
# 使用 U+2500（BOX DRAWINGS LIGHT HORIZONTAL）——项目里就是这个字符
VERSION_RE = re.compile(
    r'^[ \t]*──[ \t]*(v\d+(?:\.\d+)?'
    r'(?:[ \t]*[（(][^）)\n]*[）)])?)[ \t]*──[ \t]*$',
    re.MULTILINE,
)

# 已迁移检测——含以下任一子串即跳过（幂等）
_MIGRATED_MARKERS = ("完整变更历史见", "完整日志见")


# ──────────────────────────────────────────────────────────
#  核心逻辑
# ──────────────────────────────────────────────────────────
def _version_key(header: str) -> tuple[int, int]:
    """
    从版本头提取排序键。

    ── v7.1（KISS 加固）── → (7, 1)
    ── v6 ──              → (6, 0)
    ── v6.0（vs v5.0）──  → (6, 0)  # 只取第一个 v
    """
    m = re.search(r'v(\d+)(?:\.(\d+))?', header)
    if not m:
        return (0, 0)
    major = int(m.group(1))
    minor = int(m.group(2)) if m.group(2) else 0
    return (major, minor)


def split_top_docstring(src: str) -> tuple[str, str, str] | None:
    """
    拆分源文件为 (leading, body, rest)。
    - leading：docstring 之前的空白（几乎总是 ""）
    - body：docstring 内容（不含前后引号）
    - rest：docstring 之后的内容（含 `三个"` 之后的换行和 import）
    """
    stripped = src.lstrip()
    leading = src[: len(src) - len(stripped)]
    if not stripped.startswith('"""'):
        return None
    end = stripped.find('"""', 3)
    if end == -1:
        return None
    return leading, stripped[3:end], stripped[end + 3 :]


def extract_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """
    从 docstring body 里提取模块说明（prefix）和版本段列表。

    Returns:
        (prefix, sections)
        - prefix：模块说明 + 第一个版本头之前的内容（去掉 "Changelog:" 标题行）
        - sections：[(版本头, 完整段落), ...]  按原文顺序
    """
    matches = list(VERSION_RE.finditer(body))
    if not matches:
        return body, []

    # prefix 是第一个版本头**之前**的所有内容
    first_line_start = body.rfind("\n", 0, matches[0].start()) + 1
    prefix = body[:first_line_start]

    # 去掉 prefix 尾部的 "Changelog:" 标题行（保留它上面模块说明）
    cl = re.search(r'^[ \t]*Changelog:[ \t]*$', prefix, re.MULTILINE)
    if cl:
        prefix = prefix[: cl.start()].rstrip() + "\n"

    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        # 段从版本头所在行的**行首**开始
        start = body.rfind("\n", 0, m.start()) + 1
        # 段到下一个版本头所在行的**行首**结束（或 body 末尾）
        if i + 1 < len(matches):
            end = body.rfind("\n", 0, matches[i + 1].start()) + 1
        else:
            end = len(body)
        section_text = body[start:end].rstrip()
        header = m.group(1).strip()
        sections.append((header, section_text))

    return prefix, sections


def _clean_prefix(prefix: str) -> str:
    """去掉 prefix 尾部空行；压缩 3+ 连续空行为 2。"""
    prefix = re.sub(r'\n{3,}', '\n\n', prefix)
    return prefix.rstrip()


def _clean_rest(rest: str) -> str:
    """
    清理 docstring 之后的内容：
    - 保证以至少一个换行开头（避免 `三个"import` 语法错误）
    - 压缩 3+ 前导空行为 2（PEP 8：docstring 后一个空行）
    """
    if not rest:
        return rest
    if not rest.startswith("\n"):
        rest = "\n" + rest
    rest = re.sub(r'^\n{3,}', '\n\n', rest)
    return rest


def process_one(path: Path, cwd: Path) -> dict | None:
    """
    处理单个文件。返回 None 表示跳过（无版本段 / 已迁移 / 排除 / 无法解析）。
    """
    # 计算相对路径（用于排除清单和日志）
    try:
        rel = str(path.relative_to(cwd))
    except ValueError:
        rel = str(path)

    if rel in EXCLUDE_PATHS:
        return None

    src = path.read_text(encoding="utf-8")
    parts = split_top_docstring(src)
    if parts is None:
        return None

    leading, body, rest = parts

    # 幂等：已含指针 → 跳过
    if any(marker in body for marker in _MIGRATED_MARKERS):
        return None

    prefix, sections = extract_sections(body)
    if len(sections) < 2:
        return None  # 只有 0 或 1 段——没有可迁的

    # 按版本号倒序（主版本 + 次版本）
    sections_sorted = sorted(
        sections, key=lambda x: _version_key(x[0]), reverse=True
    )
    latest_header, latest_text = sections_sorted[0]
    older = sections_sorted[1:]

    # 构造新 docstring——空 prefix 时不留前导空行
    parts_new = [_clean_prefix(prefix), latest_text.strip(), POINTER]
    new_body = "\n\n".join(p for p in parts_new if p) + "\n"

    new_src = leading + '"""' + new_body + '"""' + _clean_rest(rest)

    return {
        "path": path,
        "rel": rel,
        "new_src": new_src,
        "all_sections_sorted": sections_sorted,  # md 里放全部（含最新）
        "kept_header": latest_header,
        "moved_headers": [h for h, _ in older],
        "old_lines": len(body.splitlines()),
        "new_lines": len(new_body.splitlines()),
    }


# ──────────────────────────────────────────────────────────
#  CHANGELOG.md 写入
# ──────────────────────────────────────────────────────────
def _write_changelog(cl_path: Path, results: list[dict]) -> None:
    """
    写 CHANGELOG.md。
    包含完整历史（含最新段）——md 是权威档案，源文件是精简视图。
    """
    header = (
        "# Changelog\n\n"
        "本项目采用 **per-module changelog**——每个模块的历史变更集中在此。\n"
        "代码里只保留「最新一段 + 指针」，完整历史在这里。\n\n"
        "---\n\n"
    )
    chunks = [header]
    for r in results:
        chunks.append(f"## `{r['rel']}`\n\n")
        for _header_text, section_text in r["all_sections_sorted"]:
            chunks.append(section_text.rstrip() + "\n\n")
        chunks.append("---\n\n")
    cl_path.write_text("".join(chunks), encoding="utf-8")


# ──────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────
def _resolve_path(p: str, cwd: Path) -> Path:
    """把相对/绝对路径统一解析为 Path。"""
    path = Path(p)
    return path if path.is_absolute() else (cwd / path).resolve()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Changelog 迁移脚本——把模块 docstring 里的旧版本段迁至 CHANGELOG.md",
    )
    ap.add_argument("--root", default="src/task_planner", help="扫描根目录（默认 src/task_planner）")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    ap.add_argument("--changelog", default="CHANGELOG.md", help="CHANGELOG.md 路径")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 CHANGELOG.md")
    args = ap.parse_args()

    cwd = Path.cwd()
    root = _resolve_path(args.root, cwd)
    cl_path = _resolve_path(args.changelog, cwd)

    if not root.is_dir():
        print(f"❌ root 不是目录: {root}", file=sys.stderr)
        return 2

    # --apply 时检查 CHANGELOG.md 冲突
    if args.apply and cl_path.exists() and not args.force:
        print(
            f"❌ {cl_path} 已存在。使用 --force 覆盖，或先删除。",
            file=sys.stderr,
        )
        return 1

    # 扫描
    results: list[dict] = []
    for path in sorted(root.rglob("*.py")):
        if "abandon" in path.parts:
            continue
        r = process_one(path, cwd)
        if r is not None:
            results.append(r)

    if not results:
        print("没有需要迁移的文件")
        return 0

    # 打印汇总表
    print(f"{'文件':<56} {'旧':>4} {'新':>4} {'迁走':>4}   保留 → 迁走")
    print("─" * 110)
    for r in results:
        rel_short = r["rel"].replace("src/task_planner/", "")
        moved = ", ".join(r["moved_headers"])
        print(
            f"{rel_short:<56} "
            f"{r['old_lines']:>4} "
            f"{r['new_lines']:>4} "
            f"{len(r['moved_headers']):>4}   "
            f"{r['kept_header']}  →  {moved or '(无)'}"
        )

    total_old = sum(r["old_lines"] for r in results)
    total_new = sum(r["new_lines"] for r in results)
    print("─" * 110)
    print(
        f"总计 {len(results)} 个文件，docstring 行数 "
        f"{total_old} → {total_new}（-{total_old - total_new}）"
    )

    if not args.apply:
        print(f"\n（dry-run。加 --apply 写入。目标: {cl_path}）")
        return 0

    # 写入源文件
    for r in results:
        r["path"].write_text(r["new_src"], encoding="utf-8")
    # 写入 CHANGELOG.md
    _write_changelog(cl_path, results)

    print(f"\n已更新 {len(results)} 个源文件")
    print(f"已写入 {cl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
