"""view.py — 快照提取 + 完成判断（纯函数，无 I/O）"""
from __future__ import annotations

import time
from typing import Any, Optional

from task_planner.services.view_model import FRONTEND_FIELDS


def extract_snapshot(
    event: dict[str, Any],
    session: Any,   # TaskSession（避免循环 import）
    mode: str = "values",
) -> Optional[dict[str, Any]]:
    """从 graph event 中提取前端需要的快照"""
    if mode == "updates":
        return None

    elapsed = time.time() - session.start_time
    view = {k: event.get(k) for k in FRONTEND_FIELDS}
    view.update({
        "type": "progress",
        "task_id": event.get("task_id", ""),
        "elapsed": round(elapsed, 1),
        "nodes": event.get("nodes") or [],
        "edges": event.get("edges") or [],
    })
    return view


def is_graph_finished(event_data: dict[str, Any], mode: str) -> bool:
    """
    轻量级完成判断（同步，纯函数）。
      - 取消/错误 → 结束
      - 节点索引超出范围 → 结束
      - 直接回答（且无节点）→ 结束
    """
    if mode != "values":
        return False

    if event_data.get("cancel_requested"):
        return True
    if event_data.get("error"):
        return True

    nodes = event_data.get("nodes", [])
    idx = event_data.get("current_node_index", 0)
    if nodes and idx >= len(nodes):
        return True

    direct_resp = event_data.get("direct_response", "")
    if direct_resp and direct_resp != "__DIRECT_RESPONSE_PENDING__":
        if not nodes:
            return True

    return False


__all__ = ["extract_snapshot", "is_graph_finished"]
