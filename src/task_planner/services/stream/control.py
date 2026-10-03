"""control.py — 任务操作接口（取消/完成/跳过/失败/可操作查询）"""
from __future__ import annotations

from typing import Any

from task_planner.infrastructure.constants import EDGE_TYPE_HARD
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.services.agent import cancel_task
from task_planner.utils.cytoscape_adapter import get_edge_type 

from .cleaner import state_cleaner

logger = get_logger("task_planner.stream")


async def get_stream_state(thread_id: str):
    """获取指定任务的状态对象"""
    return await state_cleaner.get(thread_id)


async def get_node_states(thread_id: str) -> dict[str, str]:
    """获取指定任务的节点状态字典"""
    state = await get_stream_state(thread_id)
    return await state.get_node_states() if state else {}


async def cancel_stream(thread_id: str) -> bool:
    """
    取消正在运行的流式任务。
    顺序：先通知 Agent，再标记状态，最后杀 worker。
    """
    state = await get_stream_state(thread_id)
    if not state:
        logger.warning("[StreamMgr] 取消失败：任务不存在 %s", thread_id)
        return False

    try:
        await cancel_task(thread_id)
    except Exception as e:
        logger.warning("[StreamMgr] agent.cancel_task 异常: %s", e)

    await state.mark_cancelled()
    state.abort_worker()

    logger.info("[StreamMgr] 已取消 | thread=%s", thread_id)
    return True


async def complete_node(thread_id: str, node_id: str) -> dict[str, Any]:
    """用户标记节点完成（仅更新本地 node_states）"""
    state = await get_stream_state(thread_id)
    if not state:
        return {"success": False, "error": "任务不存在或已结束"}
    await state.set_node_state(node_id, "done")
    return {"success": True, "node_states": await state.get_node_states()}


async def skip_node(thread_id: str, node_id: str, dag: dict) -> dict[str, Any]:
    """用户跳过节点，级联跳过下游（保护已完成/运行中的下游节点）"""
    state = await get_stream_state(thread_id)
    if not state:
        return {"success": False, "error": "任务不存在或已结束"}

    edges = dag.get("edges") or []
    await state.set_node_state(node_id, "skipped")

    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(str(e["from"]), []).append(str(e["to"]))

    queue = [node_id]
    visited = {node_id}
    skipped = [node_id]
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt in visited:
                continue
            visited.add(nxt)
            existing = state.node_states.get(nxt)
            if existing in ("done", "running"):
                logger.info(
                    "[StreamMgr] skip_node: 下游节点 %s 已是 %s，跳过级联",
                    nxt, existing,
                )
                continue
            await state.set_node_state(nxt, "skipped")
            skipped.append(nxt)
            queue.append(nxt)

    return {
        "success": True,
        "skipped": skipped,
        "node_states": await state.get_node_states(),
    }


async def fail_node(thread_id: str, node_id: str, dag: dict) -> dict[str, Any]:
    """用户标记节点失败，级联跳过下游（保护已完成/运行中的下游节点）"""
    state = await get_stream_state(thread_id)
    if not state:
        return {"success": False, "error": "任务不存在或已结束"}

    edges = dag.get("edges") or []
    await state.set_node_state(node_id, "failed")

    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(str(e["from"]), []).append(str(e["to"]))

    queue = [node_id]
    visited = {node_id}
    affected = [node_id]
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt in visited:
                continue
            visited.add(nxt)
            existing = state.node_states.get(nxt)
            if existing in ("done", "running"):
                logger.info(
                    "[StreamMgr] fail_node: 下游节点 %s 已是 %s，跳过级联",
                    nxt, existing,
                )
                continue
            await state.set_node_state(nxt, "skipped")
            affected.append(nxt)
            queue.append(nxt)

    return {
        "success": True,
        "affected": affected,
        "node_states": await state.get_node_states(),
    }


async def get_ready_nodes(thread_id: str, dag: dict) -> list[str]:
    """
    获取所有可操作节点（前置已完成且自身未处理）。
    ✅ Bug 修复：只把 hard 边算作硬依赖，与 auto_unlock_downstream 保持一致。
    """
    state = await get_stream_state(thread_id)
    if not state:
        return []

    node_states = await state.get_node_states()
    edges = dag.get("edges") or []
    nodes = dag.get("nodes") or []

    # ✅ 预建 {target: [sources]} 索引，仅 hard 边参与依赖判断
    in_edges: dict[str, list[str]] = {}
    for e in edges:
        if get_edge_type(e) != EDGE_TYPE_HARD:
            continue
        in_edges.setdefault(str(e["to"]), []).append(str(e["from"]))

    ready = []
    for node in nodes:
        nid = str(node["id"])
        if node_states.get(nid) in ("done", "running", "failed", "skipped"):
            continue
        deps = in_edges.get(nid, [])
        if all(node_states.get(dep) == "done" for dep in deps):
            ready.append(nid)
    return ready


__all__ = [
    "get_stream_state",
    "get_node_states",
    "cancel_stream",
    "complete_node",
    "skip_node",
    "fail_node",
    "get_ready_nodes",
]
