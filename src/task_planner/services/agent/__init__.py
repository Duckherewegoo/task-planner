"""
agent — 异步 Agent 主入口（拆分版）
═══════════════════════════════════════════════════════════════════════
对外接口与旧 agent.py 完全一致：

    from task_planner.services.agent import (
        run_task_stream,
        cancel_task,
    )

子模块：
  session    TaskSession + 会话池
  state      初始状态构建
  stream     主生成器 run_task_stream
  commands   5 个命令（取消/修改/继续/重试）
  view       快照提取 + 完成判断（纯函数）
"""
from .commands import cancel_task, modify_task, resume_task, retry_node_cmd
from .session import TaskSession, cleanup_stale_sessions, get_session
from .stream import run_task_stream

__all__ = [
    "run_task_stream",
    "cancel_task",
    "modify_task",
    "resume_task",
    "retry_node_cmd",
    "get_session",
    "cleanup_stale_sessions",
    "TaskSession",
]
