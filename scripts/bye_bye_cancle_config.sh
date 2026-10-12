#!/usr/bin/env bash
#
# replace_get_cancel_event.sh
#
# 精准匹配所有 .py 文件中的 get_cancel_event(config)，替换为 get_cancel_event()。
#
# 精准匹配规则（PCRE）：
#   (?<![A-Za-z0-9_])get_cancel_event\(config\)
#   - lookbehind 确保前面不是标识符字符（不会误伤 _get_cancel_event / my_get_cancel_event）
#   - 括号内严格匹配 config，不允许 config 前后有空格
#   - 不匹配 get_cancel_event(config, extra) 等带额外参数的形式
#
# 默认 dry-run；加 --apply 才写回（先创建 .bak 备份）。
# 依赖：GNU grep（-P 选项）、perl。
#
# 用法：
#   ./replace_get_cancel_event.sh                # dry-run，显示 diff
#   ./replace_get_cancel_event.sh --apply        # 实际写入
#   ./replace_get_cancel_event.sh --root ./src --apply
#
set -euo pipefail

ROOT="/home/dake/Documents/Projects/Life/My-AI-Journey/python_preview/deepseek-qwen/new_task_planner/"
APPLY=0

usage() {
  cat <<EOF
用法: $(basename "$0") [--root DIR] [--apply]

  默认 dry-run，只列出会改的文件和 diff。
  --apply   实际写回文件（先创建 .bak 备份）。

示例:
  $(basename "$0")                          # dry-run 当前目录
  $(basename "$0") --root ./src --apply     # 应用到 ./src
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)   ROOT="$2"; shift 2 ;;
    --apply)  APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 1 ;;
  esac
done

# ── 前置检查 ──
if [[ ! -d "$ROOT" ]]; then
  echo "错误: root 不是目录: $ROOT" >&2
  exit 1
fi

if ! echo "x" | grep -P "x" >/dev/null 2>&1; then
  echo "错误: 当前 grep 不支持 -P（PCRE）。请使用 GNU grep 或 brew install grep。" >&2
  exit 1
fi

if ! command -v perl >/dev/null 2>&1; then
  echo "错误: 需要 perl。" >&2
  exit 1
fi

# ── 精确匹配模式 ──
# 注意：模式用单引号定义，避免 shell 展开其中的特殊字符
PATTERN='(?<![A-Za-z0-9_])get_cancel_event\(config\)'
REPLACEMENT='get_cancel_event()'

# ── 排除的噪声目录 ──
EXCLUDES=(
  --exclude-dir=.git
  --exclude-dir=__pycache__
  --exclude-dir=.venv
  --exclude-dir=venv
  --exclude-dir=env
  --exclude-dir=node_modules
  --exclude-dir=.mypy_cache
  --exclude-dir=.pytest_cache
  --exclude-dir=.ruff_cache
  --exclude-dir=.tox
  --exclude-dir=.eggs
  --exclude-dir=build
  --exclude-dir=dist
  --exclude-dir=site-packages
  --exclude-dir=abandon
)

# ── 第一步：找到所有匹配的文件 ──
mapfile -t FILES < <(
  grep -rlP --include='*.py' "${EXCLUDES[@]}" "$PATTERN" "$ROOT" 2>/dev/null || true
)

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "没有找到匹配 get_cancel_event(config) 的 .py 文件"
  exit 0
fi

echo "找到 ${#FILES[@]} 个文件包含 get_cancel_event(config)："
TOTAL=0
for f in "${FILES[@]}"; do
  # -o 逐匹配输出，wc -l 精确计数（同行的多次出现也分别统计）
  n=$(grep -oP "$PATTERN" "$f" | wc -l | tr -d ' ')
  TOTAL=$((TOTAL + n))
  printf '  %s  (%s 处)\n' "$f" "$n"
done
echo "合计 $TOTAL 处。"
echo ""

# ── Dry-run：显示 diff，不写回 ──
if [[ $APPLY -eq 0 ]]; then
  echo "== dry-run: 以下为会发生的改动 =="
  for f in "${FILES[@]}"; do
    echo ""
    echo "--- $f ---"
    perl -pe "s/$PATTERN/$REPLACEMENT/g" "$f" | diff -u "$f" - || true
  done
  echo ""
  echo "（dry-run 结束。加 --apply 实际写入。）"
  exit 0
fi

# ── 第二步：应用替换 ──
echo "== 开始应用 =="
for f in "${FILES[@]}"; do
  cp -p "$f" "$f.bak"
  # perl -i 就地编辑；-p 逐行处理；s///g 一行内多次替换
  perl -i -pe "s/$PATTERN/$REPLACEMENT/g" "$f"
  echo "已替换: $f"
done

echo ""
echo "完成。备份文件后缀为 .bak。"
echo "确认无误后删除备份："
echo "  find '$ROOT' -type f -name '*.bak' -delete"
