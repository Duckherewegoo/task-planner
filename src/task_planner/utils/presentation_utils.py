"""
presentation_utils.py — 通用展示辅助函数（Dropdown 构建、任务 ID 提取）
═══════════════════════════════════════════════════════════════════════
Changelog:
  ── v1 ──
  ✅ P1-1：build_history_dropdown_options 用 wcswidth 按显示宽度截断
  ✅ P1-2：extract_first_task_id 去掉冗余的空列表判断
  ✅ P1-3：extract_first_task_id 加类型守卫

  ── v2（KISS 加固）──
  ✅ P2-1：_display_truncate 改累积宽度，从 O(n²) 降到 O(n)。
           原实现每次迭代重算 wcswidth(truncated + ch)，长文本下明显变慢。
  ✅ P2-2：_display_truncate 的省略号宽度从 _ELLIPSIS 常量推导，
           不再硬编码 1，未来换符号不用改代码。
  ✅ P2-3：修正注释——不声称"与 flowchart_pro 完全一致"，
           因为后者的 _short_label 会先清停用词，两者语义不同。
  ✅ P2-4：extract_first_task_id 的 else 分支显式拒绝 dict/tuple，
           避免 str({'a': 1}) 产出垃圾字符串。
"""
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Union,
)

from wcwidth import wcswidth

from task_planner.infrastructure.constants import HISTORY_LABEL_MAX_LEN

# 截断符号（宽度用于预留）
_ELLIPSIS = "…"


def _display_truncate(text: str, max_width: int) -> str:
    """
    按 Unicode 显示宽度截断（CJK 字符宽度为 2）。
    与 flowchart_pro._short_label 的**截断方式**类似，
    但不做停用词清理——后者用于图内标签，语义不同。

    ✅ P2-1：累积宽度，O(n) 复杂度。
    ✅ P2-2：省略号宽度由常量推导。
    """
    if not text:
        return text
    if wcswidth(text) <= max_width:
        return text

    # 省略号占位宽度
    ellipsis_w = wcswidth(_ELLIPSIS)
    if ellipsis_w < 0:
        ellipsis_w = 1
    available = max_width - ellipsis_w
    if available <= 0:
        # max_width 太小，装不下省略号 + 至少 1 字符
        return _ELLIPSIS

    truncated = ""
    current_width = 0
    for ch in text:
        w = wcswidth(ch)
        if w < 0:
            w = 1
        if current_width + w > available:
            break
        truncated += ch
        current_width += w

    return truncated + _ELLIPSIS


def build_history_dropdown_options(
    tasks: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    从任务列表构建下拉选项。

    Args:
        tasks: 任务字典列表，每个包含 'task_id' 和 'title'（可选）。

    Returns:
        适合 Dash dcc.Dropdown 的 options 列表。
    """
    options: List[Dict[str, str]] = []
    for t in tasks:
        tid = t.get("task_id")
        if not tid:
            continue   # 跳过无 ID 的任务

        raw_title = t.get("title") or t.get("raw_query") or "无标题任务"
        title = _display_truncate(str(raw_title), HISTORY_LABEL_MAX_LEN)
        tid_suffix = str(tid)[-8:]

        options.append({
            "label": f"{title} | {tid_suffix}",
            "value": tid,
        })
    return options


def extract_first_task_id(
    task_ids: Union[None, str, List[str]],
) -> Optional[str]:
    """
    安全提取第一个任务 ID。

    Args:
        task_ids: None / 字符串 / 字符串列表

    Returns:
        第一个任务 ID 字符串；无效输入返回 None。
    """
    if not task_ids:
        return None

    if isinstance(task_ids, list):
        first = task_ids[0]
        return str(first) if first else None

    if isinstance(task_ids, str):
        return task_ids

    # ✅ P2-4：dict / tuple / set 等容器不是有效的 task_id，显式拒绝
    #          int / float 等标量仍兼容转为字符串
    if isinstance(task_ids, (dict, tuple, set)):
        return None

    return str(task_ids)


__all__ = [
    "build_history_dropdown_options",
    "extract_first_task_id",
]
