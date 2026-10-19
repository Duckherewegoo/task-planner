"""save.py — 入库节点（DB 不可用时降级为本地 ID）"""

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.db import create_task_with_plan
from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

logger = get_logger(__name__)


async def save_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """
    入库节点（异步）。

    降级策略：DB 不可用时不再让整个图崩溃，而是：
      - 生成一个本地 task_id（"local-" 前缀标记，便于下游识别）
      - 记录 warning 日志
      - 继续后续节点（render / execute）

    这样评估、CI、本地 demo 无需 MongoDB 也能跑通。

    ⚠️ 降级效果依赖下游节点配合：
       task_id = "local-..." 之后，execute.py 里的 DB 调用（mark_task_running /
       update_node_status / mark_task_failed）会用这个假 ID 去查 DB——
       如果 DB 仍不可用会抛异常，若 execute.py 不吞掉这些异常，
       "降级"只把崩溃点从 save 挪到了 execute。
       execute.py 那边已用 _safe_db_update 包裹这些调用，
       两侧共同保证"DB 不可用时全链路可跑"。

    ⚠️ 宽捕获是有意的——任何 DB 异常（连接、超时、权限、pymongo 自定义异常）
       都应触发降级。代价：如果 create_task_with_plan 内部有代码 bug
       （TypeError / AttributeError 等），宽捕获会把它当成"DB 故障"处理，
       降级后继续跑，掩盖真 bug。
       排查时请留意 warning 日志里 err 的类型——
       非 IOError 类异常（如 TypeError）通常指向代码 bug 而非 DB 故障。
    """
    rid = set_req_id()
    logger.info("[Graph] save_node (req=%s)", rid)

    try:
        task_doc, _plan_doc = await create_task_with_plan(
            raw_query=state["user_input"],
            intent_info=state["intent"],
            plan_data={"nodes": state["nodes"], "edges": state["edges"]},
            req_id=rid,
        )
        task_id = task_doc["task_id"]
        logger.info("[Graph] save_node 入库成功: %s", task_id)
        return {
            "task_id": task_id,
            "steps": state.get("steps", []) + [f"已入库: {task_id}"],
        }

    except Exception as e:
        fallback_id = f"local-{uuid.uuid4().hex[:12]}"
        logger.warning(
            "[Graph] save_node DB 写入失败，降级为本地 ID: %s | err=%s",
            fallback_id, e,
        )
        return {
            "task_id": fallback_id,
            "steps": state.get("steps", []) + [f"⚠️ 未入库(降级): {fallback_id}"],
            # Patch：删掉 "error": ""。
            # 原写法有两层问题：
            # 1. 空操作——create_initial_state 里 error 本就是 ""，
            # 返回它等于返回旧值。
            # 2. 意图相反——如果上游某节点设置了 error（未来扩展），
            # 这里会把那个错误静默清空，与注释说的"不污染"恰好相反。
            # 省略 error 键 → LangGraph 保留 state 里的现有值，
            # 这才是真正的"不污染"。"这不是错误"的语义已由
            # steps 里的 "⚠️ 未入库(降级)" 表达。
        }
