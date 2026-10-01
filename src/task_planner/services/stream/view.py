"""view.py — 视图层辅助函数（纯函数）"""
from __future__ import annotations

from task_planner.utils.cytoscape_adapter import dag_to_cytoscape


def snapshot_to_elements(snapshot: dict) -> list:
    """从 snapshot 提取 elements（供 Dash Cytoscape 渲染）"""
    nodes = snapshot.get("nodes") or []
    edges = snapshot.get("edges") or []
    if not nodes:
        return []
    node_states = snapshot.get("node_states") or {}
    return dag_to_cytoscape(nodes, edges, node_states)


def get_status_text(snapshot: dict) -> str:
    """
    从 snapshot 提取状态文本（供前端状态栏展示）。
    优先从字段推导，不依赖 agent 未发送的 type 值。
    """
    if not snapshot:
        return "等待中..."

    # ── 强信号优先 ──
    if snapshot.get("error"):
        return f"❌ {str(snapshot['error'])[:50]}"

    if snapshot.get("cancel_requested"):
        return "⏹️ 已停止"

    direct_resp = snapshot.get("direct_response", "")
    if direct_resp and direct_resp != "__DIRECT_RESPONSE_PENDING__":
        return f"✅ {snapshot.get('status_text') or '已完成'}"

    # ── 有节点：按进度 ──
    nodes = snapshot.get("nodes") or []
    idx = snapshot.get("current_node_index", 0)
    if nodes:
        if idx >= len(nodes):
            return "✅ 任务完成"
        return f"🔄 执行中 ({idx}/{len(nodes)})"

    # ── 回退到 type ──
    snap_type = snapshot.get("type", "")
    msg = snapshot.get("message", "")
    elapsed = snapshot.get("elapsed") or 0

    if snap_type == "start":
        return "🚀 任务已提交"
    if snap_type == "progress":
        if msg and elapsed:
            return f"{msg} ({elapsed:.0f}s)"
        return msg or "处理中..."
    if snap_type == "interrupt":
        return f"⏸️ {msg or '等待用户操作'}"
    if snap_type == "complete":
        return f"✅ {snapshot.get('status_text') or '完成'}"
    if snap_type == "cancelled":
        return "⏹️ 已停止"
    if snap_type == "timeout":
        return f"⏰ {msg or '超时'}"
    if snap_type == "error":
        return f"❌ {msg or '错误'}"
    if snap_type == "resume":
        return f"🔄 正在恢复执行... {msg}"

    return snapshot.get("status_text") or "处理中..."


__all__ = ["snapshot_to_elements", "get_status_text"]
