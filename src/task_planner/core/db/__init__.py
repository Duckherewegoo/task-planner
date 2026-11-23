"""
db — MongoDB 异步数据层（Motor / FastAPI backend）

对外接口（函数名与参数与旧 database.py 一致，但全部为 async，调用方需 await）：

    from task_planner.core.db import get_task
    task = await get_task(task_id)

⚠️ 迁移提示：旧代码 `db = get_db()` 现在必须 `await get_db()`，
   否则拿到的是 coroutine 对象，后续 `db["tasks"]` 会 TypeError。

子模块：
  client    连接生命周期（init_db / get_db / close_db，均为 async）
  schema    数据清洗 / 状态辅助（同步，纯 CPU）
  plans     Plan CRUD
  tasks     Task CRUD + 状态更新
  nodes     nodes 数组操作
"""

from .client import close_db, get_db, init_db
from .nodes import get_node_status, reset_node_status, update_node_status
from .plans import create_plan, delete_plan, delete_plans, get_plan
from .schema import TERMINAL_STATUSES, safe_int, status_name, validate_plan
from .tasks import (
    batch_delete_tasks,
    create_direct_answer_task,
    create_task_with_plan,
    delete_task,
    get_recent_tasks,
    get_task,
    list_tasks,
    load_task_with_plan,
    mark_task_failed,
    mark_task_running,
    mark_task_success,
    mark_task_timeout,
)

__all__ = [
    # 连接
    "init_db",
    "get_db",
    "close_db",
    # Plan
    "create_plan",
    "get_plan",
    "delete_plan",
    "delete_plans",
    # Task 创建/查询
    "create_task_with_plan",
    "create_direct_answer_task",
    "get_task",
    "load_task_with_plan",
    "list_tasks",
    "get_recent_tasks",
    # Task 状态
    "mark_task_success",
    "mark_task_failed",
    "mark_task_timeout",
    "mark_task_running",
    # Task 删除
    "delete_task",
    "batch_delete_tasks",
    # 节点
    "update_node_status",
    "reset_node_status",
    "get_node_status",
    # Schema 辅助（保留以便测试）
    "validate_plan",
    "status_name",
    "safe_int",
    "TERMINAL_STATUSES",
]
