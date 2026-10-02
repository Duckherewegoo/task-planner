"""schema.py — 数据清洗 / 状态名辅助 / 常量"""
from __future__ import annotations

from typing import Any

from task_planner.infrastructure.constants import TASK_STATUS
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.core.db.schema")

# 状态码 → 名字（反查表）
_STATUS_NAMES: dict[int, str] = {v: k for k, v in TASK_STATUS.items()}

# 终态集合：进入这些状态后不可再被"成功"覆盖
TERMINAL_STATUSES = frozenset({
    TASK_STATUS["SUCCESS"],   # 2
    TASK_STATUS["FAILED"],    # 3
    TASK_STATUS["TIMEOUT"],   # 4
    TASK_STATUS["SKIPPED"],   # 5
})


def status_name(code: int) -> str:
    return _STATUS_NAMES.get(code, f"?({code})")


def safe_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def validate_plan(plan_data: dict[str, Any]) -> dict[str, Any]:
    """
    清洗 nodes/edges，确保 ID 合法，补全字段。
    返回新 dict，不原地修改入参。
    丢弃的边会记录原因。
    """
    nodes_raw = list(plan_data.get("nodes", []) or [])
    edges_raw = list(plan_data.get("edges", []) or [])

    clean_nodes = []
    for i, n in enumerate(nodes_raw, 1):
        clean_nodes.append({
            "id": safe_int(n.get("id"), default=i),
            "name": str(n.get("name", f"步骤{i}")),
            "details": str(n.get("details", "") or n.get("detail", "")),
            "status": safe_int(n.get("status"), default=0),
        })

    id_set = {n["id"] for n in clean_nodes}
    clean_edges = []
    for e in edges_raw:
        f = safe_int(e.get("from"), default=0)
        t = safe_int(e.get("to"), default=0)

        if f not in id_set:
            logger.warning("[DB] 丢弃无效边（from 节点不存在）: %s→%s", f, t)
            continue
        if t not in id_set:
            logger.warning("[DB] 丢弃无效边（to 节点不存在）: %s→%s", f, t)
            continue
        if f == t:
            logger.warning("[DB] 丢弃自环边: %s→%s", f, t)
            continue

        clean_edges.append({
            "from": f,
            "to": t,
            "label": str(e.get("label", "")),
        })

    logger.debug(
        "[DB] Plan 清洗完成: nodes=%d(原始%d), edges=%d(原始%d, 丢弃%d)",
        len(clean_nodes), len(nodes_raw),
        len(clean_edges), len(edges_raw),
        len(edges_raw) - len(clean_edges),
    )

    return {
        **plan_data,
        "nodes": clean_nodes,
        "edges": clean_edges,
        "task_name": plan_data.get("task_name", "未命名计划"),
        "description": plan_data.get("description", ""),
    }


__all__ = [
    "TERMINAL_STATUSES",
    "status_name",
    "safe_int",
    "validate_plan",
]
