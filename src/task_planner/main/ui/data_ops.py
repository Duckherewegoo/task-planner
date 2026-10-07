"""data_ops.py — 数据源解析 / 节点状态更新 / 历史回填"""
from __future__ import annotations

import time

from task_planner.core.db import load_task_with_plan
from task_planner.infrastructure.constants import (
    EDGE_TYPE_HARD,
    NODE_ACTION_CONFIG,
)
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.utils.cytoscape_adapter import dag_to_cytoscape, get_edge_type
from task_planner.utils.presentation_utils import extract_first_task_id

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  数据源解析
# ═══════════════════════════════════════════════════════════════════
async def resolve_dag_data(
    history_task_ids,
    dag_store,
    node_states_store=None,
):
    """
    统一数据源解析（双源适配器）。
    优先级：历史持久化数据 > 当前内存 Store。
    """
    task_id = None
    if history_task_ids:
        task_id = (
            history_task_ids[0]
            if isinstance(history_task_ids, list)
            else history_task_ids
        )

    if task_id:
        try:
            data = await load_task_with_plan(task_id)
        except Exception as e:
            logger.error(
                "[Export] 加载历史任务失败 task=%s: %s",
                task_id, e, exc_info=True,
            )
            data = None

        if data:
            plan = data.get("plan") or {}
            nodes = plan.get("nodes") or []
            edges = plan.get("edges") or []
            node_states = data.get("node_states") or node_states_store
            task_id_str = str(task_id)[-8:]
            return nodes, edges, node_states, task_id_str
        else:
            logger.warning("[Export] 未找到历史任务数据: %s", task_id)
            return None, None, None, None

    nodes = (dag_store or {}).get("nodes") or []
    edges = (dag_store or {}).get("edges") or []
    return nodes, edges, node_states_store, "current"


# ═══════════════════════════════════════════════════════════════════
#  节点状态更新
# ═══════════════════════════════════════════════════════════════════
def update_node_and_render(
    dag: dict,
    node_states: dict,
    nid: str,
    target_status: str,
) -> tuple:
    """
    统一节点状态更新引擎。
    根据 NODE_ACTION_CONFIG 自动处理下游解锁、渲染和统计。
    """
    config = NODE_ACTION_CONFIG.get(target_status)
    if not config:
        raise ValueError(f"未知的节点操作类型: {target_status}")

    new_states = dict(node_states)
    new_states[nid] = target_status

    if config["unlock_downstream"]:
        new_states = auto_unlock_downstream(dag, new_states, nid, target_status)

    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    elements = dag_to_cytoscape(nodes, edges, new_states)

    total = len(nodes)
    counts = {
        s: sum(1 for v in new_states.values() if v == s)
        for s in ("done", "skipped", "failed")
    }
    processed = counts["done"] + counts["skipped"]

    status_msg = (
        f"{config['icon']} 节点 {nid} 已标记{config['label']} | "
        f"进度: {processed}/{total} "
        f"(完成:{counts['done']} 跳过:{counts['skipped']} 失败:{counts['failed']})"
    )

    return new_states, elements, status_msg


def auto_unlock_downstream(
    dag,
    node_states,
    changed_nid,
    new_state,
    allowed_edge_types=None,
):
    """通用下游建议性解锁引擎"""
    edges = dag.get("edges", [])
    new_states = dict(node_states or {})

    if allowed_edge_types is not None:
        check_edge_types = set(allowed_edge_types)
    else:
        check_edge_types = {EDGE_TYPE_HARD}

    downstream_edges_map: dict[str, list] = {}
    for e in edges:
        src, tgt = str(e.get("from", "")), str(e.get("to", ""))
        if src == changed_nid and get_edge_type(e) in check_edge_types:
            downstream_edges_map.setdefault(tgt, []).append(e)

    for tgt, relevant_in_edges in downstream_edges_map.items():
        current_state = new_states.get(tgt, "pending")
        if current_state not in ("pending", "blocked"):
            continue
        all_satisfied = all(
            new_states.get(str(e["from"])) == "done"
            for e in relevant_in_edges
        )
        if all_satisfied:
            new_states[tgt] = "running"

    return new_states


# ═══════════════════════════════════════════════════════════════════
#  历史回填
# ═══════════════════════════════════════════════════════════════════
async def load_and_fill_query(task_ids, action_label: str) -> tuple:
    """统一历史任务回填引擎"""
    from dash import no_update

    task_id = extract_first_task_id(task_ids)

    if not task_id:
        return "⚠️ 请先选择历史任务", no_update, no_update

    try:
        data = await load_task_with_plan(task_id)
    except Exception as e:
        logger.error(
            "[HistoryAction] %s 加载失败 task=%s: %s",
            action_label, task_id, e, exc_info=True,
        )
        return "❌ 任务数据加载失败，请稍后重试", no_update, no_update

    if not data:
        return "❌ 任务不存在或已被删除", no_update, no_update

    raw_query = (data.get("task") or {}).get("raw_query", "").strip()
    if not raw_query:
        return "⚠️ 该任务无原始需求文本，无法操作", no_update, no_update

    if action_label == "继续执行":
        msg = f"▶️ 已回填上次需求（{len(raw_query)}字），可直接继续或修改后重新规划"
    else:
        msg = f"🔄 [{action_label}] 已回填原始需求（{len(raw_query)}字），请确认后点击「开始规划」"

    logger.info(
        "[HistoryAction] %s 回填成功 task=%s query_len=%d",
        action_label, task_id, len(raw_query),
    )
    return msg, "tab-new", raw_query


# ═══════════════════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════════════════
def make_filename(content_type: str, task_id_str: str, ext: str) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{content_type}_{task_id_str}_{timestamp}.{ext}"


def safe_elapsed(task_start_time, now) -> float:
    try:
        start = float(task_start_time)
        if start <= 0:
            return 0.0
        return round(now - start, 1)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "resolve_dag_data",
    "update_node_and_render",
    "auto_unlock_downstream",
    "load_and_fill_query",
    "make_filename",
    "safe_elapsed",
]
