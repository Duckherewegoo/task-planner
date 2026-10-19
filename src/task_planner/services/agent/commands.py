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
       retry_node_index     ← RETRY_NODE 时的节点标识（仅 RETRY_NODE 携带）

   这些键名与 state.py 的 TaskState 字段名一致——因为 interrupt 的
   返回契约与 state 字段共享同一套命名。任何一处漂移都会让对应命令
   静默失效（被当成 CONTINUE 处理，用户点击无效果）。

   对称通道见 workflow.resume_graph_async——它走 update_state，
   键名规则与本模块相同。

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
       user_action / modified_input / retry_node_index 三个键名。

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
            thread_id, type(new_input).__name__,
        )
        return False

    await _resume_with_command(session, {
        # 键名对齐 execute.execute_node 的 feedback.get("user_action")
        "user_action": UserAction.MODIFY,
        # 键名对齐 execute.execute_node 的 feedback.get("modified_input")
        "modified_input": cleaned,
    })
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

    await _resume_with_command(session, {
        # 键名对齐 execute.execute_node 的 feedback.get("user_action")
        "user_action": UserAction.CONTINUE,
    })
    return True


async def retry_node_cmd(thread_id: str, node_index: int) -> bool:
    """
    重试指定节点（异步）。

    语义：用户对某节点结果不满意，让图重新执行该节点。
    执行路径：走 resume 通道，feedback 携带 RETRY_NODE 动作与目标标识。
    execute.execute_node 读到 RETRY_NODE 后：
      - 保持 current_node_index 不变（不前进）
      - 由 _resolve_target_index 按 retry_node_index 裁决本轮执行的目标

    ⚠️ node_index 的实际语义取决于 execute._resolve_target_index 的裁决规则：
       优先按 **index** 解释（0..len(nodes)-1），越界时尝试按
       **node["id"]** 匹配，都失败则降级 current_node_index。
       调用方（API / UI）需明确传的是哪一种——当前 UI 传的是
       用户在列表里点击的位置（index 语义）。

       TODO: 统一语义——要么 UI 传 id，execute 优先按 id 解释；
             要么 UI 传 index，参数名改 node_index。当前是混合状态。

    Args:
        thread_id: 会话 ID
        node_index:   目标节点标识（见上，当前 UI 用 index 语义）

    Returns:
        True  — 命令已接受
        False — 无会话
    """
    session = await get_session(thread_id)
    if not session:
        return False

    await _resume_with_command(session, {
        # 键名对齐 execute.execute_node 的 feedback.get("user_action")
        "user_action": UserAction.RETRY_NODE,
        # 键名对齐 state.TaskState 的 retry_node_index 字段
        # （原写法 "node_index" 永远读不到——execute 读的是 retry_node_index）
        "retry_node_index": node_index,
    })
    return True


__all__ = [
    "cancel_task",
    "modify_task",
    "resume_task",
    "retry_node_cmd",
]
