"""commands.py — 任务命令（取消 / 修改 / 继续 / 重试）

职责：
  提供面向 API / UI 的任务控制命令。四个命令分成两条通道：

    ┌─ cancel_task       → 不走 resume，直接设 cancel_event（异步打断）
    └─ modify / resume / retry
                         → 走 resume 通道：graph.ainvoke(Command(resume=...))
                           恢复挂起的 interrupt，让图继续执行

⚠️ resume 通道的 feedback 键名契约（本模块为写入方）：
   通过 Command(resume=...) 传入的 dict，会被 execute.execute_node 里
   的 interrupt() 返回值（feedback）接收，读取端固定使用以下键名：

       user_action       ← 动作（UserAction 枚举或等值字符串）
       modified_input    ← MODIFY 时的新输入（仅 MODIFY 携带）
       retry_node_id     ← RETRY_NODE 时的目标节点 ID（仅 RETRY_NODE 携带）
                          注意：是节点 ID 而非索引。execute._resolve_target_index
                          只按 node["id"] 匹配（int/str 双兼容），不走 index 语义。

   这些键名与 state.py 的 TaskState 字段名一致。任何一处漂移都会让
   对应命令静默失效（被当成 CONTINUE 处理，用户点击无效果）。

⚠️ 跨模块通道：
   - commands.py 直接 graph.ainvoke(Command(resume=...)) —— 面向 API / UI
   - workflow.resume_graph_async 用 astream(Command(resume=...)) —— 面向 stream 层
   两者都是 Command 路径，feedback 键名规则一致。

⚠️ 跨文件依赖：
   - execute.execute_node 读 feedback 的键名规则（上面已列）
   - state.TaskState 的字段名规则（commands 与 state 共享）
   - utils.context.cancel_event_var（cancel_task 设置、resume 绑定）
   修改以上任一处，必须回来同步本模块。
"""

import contextvars
from typing import Any

from langgraph.types import Command

from task_planner.core.graph.nodes import sanitize_input
from task_planner.core.graph.state import UserAction
from task_planner.core.graph.workflow import graph
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.utils.context import cancel_event_var

from .session import TaskSession, get_session

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 内部：resume 执行入口
# ═══════════════════════════════════════════════════════════════════
async def _resume_with_command(
    session: TaskSession,
    resume_data: dict[str, Any],
) -> None:
    """
    用 graph.ainvoke(Command(resume=...)) 恢复图执行。

    两个必须做的事：
      1. 把 session.cancel_event 绑定到 contextvar——
         execute.intent 等节点通过 get_cancel_event() 读它。
         取消事件从 contextvar 读，不再经过 config。
         Command 恢复走的是一次新的 invoke，contextvar 不会自动继承——
         必须显式 copy_context() 后 run。
      2. 传 session.config（含 thread_id）给 ainvoke——
         LangGraph 靠它定位 checkpoint。

    ⚠️ resume_data 的键名契约：
       见模块 docstring。写入方（本函数调用者）必须严格遵守
       user_action / modified_input / retry_node_id 三个键名。

    Args:
        session:     任务会话（含 config / cancel_event）
        resume_data: 传给 interrupt() 返回值的 dict，键名见上

    Raises:
        不捕获任何异常——让调用方决定是否重试。
        当前调用方（modify / resume / retry）都不重试，
        让异常冒泡给 API 层。
    """
    ctx = contextvars.copy_context()
    ctx.run(cancel_event_var.set, session.cancel_event)
    await graph.ainvoke(Command(resume=resume_data), session.config)


# ═══════════════════════════════════════════════════════════════════
# 公开命令
# ═══════════════════════════════════════════════════════════════════
async def cancel_task(thread_id: str) -> bool:
    """
    取消任务（异步）。

    ⚠️ 当前只做协作式取消（设 cancel_event）——不强制打断正在 await 的 IO。
       TaskSession 不持有 runner task（那是 TaskStreamState 的事），
       强制取消需要通过 state_cleaner 拿 stream 层状态——见
       ARCHITECTURE_FUTURE.md 的 TODO。
    """
    session = await get_session(thread_id)
    if session:
        session.cancel_event.set()
        logger.info("[Agent] 取消信号已发送 | thread=%s", thread_id)
        return True
    return False


async def modify_task(thread_id: str, new_input: str) -> bool:
    """
    修改任务需求（异步）。

    语义：用户中断当前执行、给出新需求，让图重新走 intent → plan → ...
    执行路径：走 resume 通道，feedback 携带 MODIFY 动作与新输入。
    execute.execute_node 读到 MODIFY 后清空规划产物、重置 current_node_index=0，
    并设 needs_planning=True 让 intent 重新判断。

    ⚠️ 输入处理：
       sanitize_input 对**任何输入**都返回 str（非 str 输入返回 ""），
       不抛异常。所以不能用 try/except 捕获"非法输入"——必须显式
       检查清洗结果是否为空。
       清洗后为空（原值为 None / 空串 / 全被脱敏）→ 拒绝，不修改。

    Args:
        thread_id: 会话 ID
        new_input: 用户新需求（任意类型，内部会过 sanitize_input）

    Returns:
        True  — 命令已接受，图将重新规划
        False — 无会话 / 清洗后为空
    """
    session = await get_session(thread_id)
    if not session:
        return False

    # 修正：sanitize_input 不抛异常——它把非 str 输入转成空串。
    # 显式检查空结果，而不是靠 except ValueError（那是死代码）。
    cleaned = sanitize_input(new_input)
    if not cleaned:
        logger.warning(
            "[Agent] 修改输入为空或全被脱敏，拒绝 | thread=%s | raw_type=%s",
            thread_id,
            type(new_input).__name__,
        )
        return False

    await _resume_with_command(
        session,
        {
            # 键名对齐 execute.execute_node 的 feedback.get("user_action")
            "user_action": UserAction.MODIFY,
            # 键名对齐 execute.execute_node 的 feedback.get("modified_input")
            "modified_input": cleaned,
        },
    )
    return True


async def resume_task(thread_id: str) -> bool:
    """
    继续执行（异步）。

    语义：用户在 interrupt 处点"继续"，让图执行下一节点。
    执行路径：走 resume 通道，feedback 携带 CONTINUE 动作。
    execute.execute_node 读到 CONTINUE 后 current_node_index += 1。

    Args:
        thread_id: 会话 ID

    Returns:
        True  — 命令已接受
        False — 无会话
    """
    session = await get_session(thread_id)
    if not session:
        return False

    await _resume_with_command(
        session,
        {
            # 键名对齐 execute.execute_node 的 feedback.get("user_action")
            "user_action": UserAction.CONTINUE,
        },
    )
    return True


async def retry_node_cmd(thread_id: str, node_id: int | str) -> bool:
    """重试指定节点（异步）。

    语义：用户对某节点结果不满意，让图重新执行该节点。
    执行路径：走 resume 通道，feedback 携带 RETRY_NODE 动作与目标节点 ID。

    execute.execute_node 读到 RETRY_NODE 后：
      - 从 feedback 读取 retry_node_id
      - 遍历 nodes 按 node["id"] 匹配，找到目标节点的 index
      - 设置 current_node_index = 目标 index（下一轮 _resolve_target_index
        会基于 current_node_index 定位）
      - 未找到目标 id 时降级为原地重试当前节点

    ⚠️ node_id 的语义：节点 ID，不是列表索引。
       execute._resolve_target_index 只按 node["id"] 匹配（int/str 双兼容）。
       调用方（API / UI）需传节点实际的 id 值，而非在列表中的位置。

    Args:
        thread_id: 会话 ID
        node_id:   目标节点的 ID（int 或 str）

    Returns:
        True  — 命令已接受
        False — 无会话
    """
    session = await get_session(thread_id)
    if not session:
        return False

    await _resume_with_command(
        session,
        {
            "user_action": UserAction.RETRY_NODE,
            "retry_node_id": node_id,
        },
    )
    return True


__all__ = [
    "cancel_task",
    "modify_task",
    "resume_task",
    "retry_node_cmd",
]
