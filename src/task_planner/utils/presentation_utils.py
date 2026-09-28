"""
presentation_utils.py — 通用展示辅助函数（Dropdown 构建、任务 ID 提取）

Changelog:
  ✅ P1-1：build_history_dropdown_options 用 wcswidth 按显示宽度截断，
           中文不再因字符数截断而溢出。
  ✅ P1-2：extract_first_task_id 去掉冗余的空列表判断。
  ✅ P1-3：extract_first_task_id 加类型守卫，非 str 非 list 输入返回 None。
"""
from typing import Any, Dict, List, Optional, Union

from wcwidth import wcswidth

from task_planner.infrastructure.config import HISTORY_LABEL_MAX_LEN


def _display_truncate(text: str, max_width: int) -> str:
    """
    按 Unicode 显示宽度截断（CJK 字符宽度为 2）。
    与 flowchart_pro.py / pyvis_export.py 的截断逻辑保持一致。
    """
    if not text:
        return text
    if wcswidth(text) <= max_width:
        return text

    truncated = ""
    reserve = 1   # "…" 的宽度
    for ch in text:
        w = wcswidth(ch)
        if w < 0:
            w = 1
        if wcswidth(truncated + ch) > max_width - reserve:
            break
        truncated += ch
    return truncated + "…"


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
        # ✅ P1-1 修复：按显示宽度截断
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
        第一个任务 ID 字符串，无效输入返回 None。
    """
    if not task_ids:
        return None

    if isinstance(task_ids, list):
        # ✅ P1-2 修复：外层已判非空，不需要再判
        first = task_ids[0]
        # ✅ P1-3 修复：类型守卫
        return str(first) if first else None

    if isinstance(task_ids, str):
        return task_ids

    # 其他类型（int / dict / ...）→ 统一转字符串
    return str(task_ids)
