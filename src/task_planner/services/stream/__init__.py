"""
stream — 后台流式任务管理（拆分版）
═══════════════════════════════════════════════════════════════════════
对外接口与旧 stream_manager.py 完全一致：

    from task_planner.services.stream import start_stream

子模块：
  state      TaskStreamState
  cleaner    StreamStateCleaner + state_cleaner 单例
  launcher   start_stream / resume_stream
  control    取消/完成/跳过/失败/可操作查询
  view       视图辅助（纯函数）
"""
from .cleaner import (
    StreamStateCleaner,
    state_cleaner,
)
from .control import (
    cancel_stream,
    complete_node,
    fail_node,
    get_node_states,
    get_ready_nodes,
    get_stream_state,
    skip_node,
)
from .launcher import (
    resume_stream,
    start_stream,
)
from .state import TaskStreamState
from .view import (
    get_status_text,
    snapshot_to_elements,
)

__all__ = [
    # 类
    "TaskStreamState",
    "StreamStateCleaner",
    "state_cleaner",
    # 启动 / 恢复
    "start_stream",
    "resume_stream",
    # 操作
    "cancel_stream",
    "complete_node",
    "skip_node",
    "fail_node",
    "get_ready_nodes",
    "get_stream_state",
    "get_node_states",
    # 视图
    "snapshot_to_elements",
    "get_status_text",
]
