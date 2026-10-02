"""
stream_manager.py — 【兼容壳】
实现已迁至 task_planner.services.stream 包。
保留此文件仅为向后兼容，新代码请直接 import 自 task_planner.services.stream
"""
from __future__ import annotations

from task_planner.services.stream import (
    StreamStateCleaner,
    TaskStreamState,
    cancel_stream,
    complete_node,
    fail_node,
    get_node_states,
    get_ready_nodes,
    get_stream_state,
    get_status_text,
    resume_stream,
    skip_node,
    snapshot_to_elements,
    start_stream,
    state_cleaner,
)

__all__ = [
    "TaskStreamState",
    "StreamStateCleaner",
    "state_cleaner",
    "start_stream",
    "resume_stream",
    "cancel_stream",
    "get_stream_state",
    "get_node_states",
    "complete_node",
    "skip_node",
    "fail_node",
    "get_ready_nodes",
    "snapshot_to_elements",
    "get_status_text",
]
