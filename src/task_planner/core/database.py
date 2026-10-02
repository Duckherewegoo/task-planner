"""
database.py — 【兼容壳】
实现已迁至 task_planner.core.db 包。
保留此文件仅为向后兼容，新代码请直接 import 自 task_planner.core.db
"""
from __future__ import annotations

from task_planner.core.db import (
    init_db,
    get_db,
    close_db,
    create_plan,
    get_plan,
    delete_plan,
    delete_plans,
    create_task_with_plan,
    create_direct_answer_task,
    get_task,
    load_task_with_plan,
    list_tasks,
    get_recent_tasks,
    mark_task_success,
    mark_task_failed,
    mark_task_timeout,
    mark_task_running,
    delete_task,
    batch_delete_tasks,
    update_node_status,
    reset_node_status,
    get_node_status,
    validate_plan,
    status_name,
    safe_int,
    TERMINAL_STATUSES,
    DBManager,
    db_manager,
)

__all__ = [
    "init_db", "get_db", "close_db",
    "create_plan", "get_plan", "delete_plan", "delete_plans",
    "create_task_with_plan", "create_direct_answer_task",
    "get_task", "load_task_with_plan",
    "list_tasks", "get_recent_tasks",
    "mark_task_success", "mark_task_failed",
    "mark_task_timeout", "mark_task_running",
    "delete_task", "batch_delete_tasks",
    "update_node_status", "reset_node_status", "get_node_status",
    "validate_plan", "status_name", "safe_int", "TERMINAL_STATUSES",
    "DBManager", "db_manager",
]
