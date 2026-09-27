"""
presention_utils.py — 通用展示辅助函数（Dropdown构建、任务ID提取）
"""
from typing import Any, List, Dict, Optional, Union

from task_planner.infrastructure.config import HISTORY_LABEL_MAX_LEN


def build_history_dropdown_options(tasks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    从任务列表构建下拉选项。

    Args:
        tasks: 任务字典列表，每个包含 'task_id' 和 'title'（可选）。

    Returns:
        适合 Dash dcc.Dropdown 的 options 列表。
    """
    options = []
    for t in tasks:
        raw_title = t.get("title") or t.get("raw_query") or "无标题任务"
        # 确保是字符串，并截断
        title = str(raw_title)[:HISTORY_LABEL_MAX_LEN]
        tid = t.get("task_id")
        if not tid:
            continue  # 跳过无ID的任务
        tid_suffix = str(tid)[-8:]
        options.append({
            "label": f"{title} | {tid_suffix}",
            "value": tid,
        })
    return options


def extract_first_task_id(task_ids: Union[None, str, List[str]]) -> Optional[str]:
    """
    安全提取第一个任务ID。

    Args:
        task_ids: 可以是 None、单个字符串或字符串列表。

    Returns:
        第一个任务ID字符串，如果不存在则返回 None。
    """
    if not task_ids:
        return None
    if isinstance(task_ids, list):
        return task_ids[0] if task_ids else None
    # 假定是字符串
    return str(task_ids)
