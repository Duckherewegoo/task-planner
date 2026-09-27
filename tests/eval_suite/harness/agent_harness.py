"""
Agent 运行时封装：异步调用 task_planner 的流式执行，收集输出
"""

import asyncio
import time
from typing import Dict, Any, Optional, List, AsyncGenerator
from task_planner.services.agent import run_task_stream
from task_planner.services.stream_manager import get_stream_state, cancel_stream
from task_planner.core.database import load_task_with_plan
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

logger = get_logger("eval.agent_harness")


class AgentHarness:
    """
    封装 Agent 运行，捕获执行结果、中间状态、延迟等
    """

    def __init__(self, enable_refine: bool = True, timeout: int = 300):
        self.enable_refine = enable_refine
        self.timeout = timeout
        self.thread_id = None
        self._cancelled = False

    async def run(self, user_input: str) -> Dict[str, Any]:
        """
        执行单个测试用例，返回完整结果字典
        """
        import uuid
        self.thread_id = str(uuid.uuid4())
        set_req_id(self.thread_id)

        logger.info(f"[Harness] Running test with input: {user_input[:50]}...")

        result = {
            "input": user_input,
            "thread_id": self.thread_id,
            "output": "",
            "error": None,
            "snapshots": [],
            "final_state": {},
            "nodes": [],
            "edges": [],
            "task_id": "",
            "elapsed": 0.0,
            "needs_planning": False,
            "direct_response": "",
            "success": False,
        }

        start_time = time.time()
        try:
            async for snapshot in run_task_stream(
                user_input=user_input,
                thread_id=self.thread_id,
                enable_refine=self.enable_refine,
                resume=False,
            ):
                result["snapshots"].append(snapshot)
                # 提取最终关键信息
                if snapshot.get("direct_response"):
                    result["direct_response"] = snapshot["direct_response"]
                if snapshot.get("nodes"):
                    result["nodes"] = snapshot["nodes"]
                    result["edges"] = snapshot.get("edges", [])
                if snapshot.get("task_id"):
                    result["task_id"] = snapshot["task_id"]
                if snapshot.get("needs_planning") is not None:
                    result["needs_planning"] = snapshot.get("needs_planning", False)

                # 检查是否完成/错误
                if snapshot.get("type") in ("complete", "cancelled", "error", "timeout"):
                    result["error"] = snapshot.get("error") or snapshot.get("message")
                    break

            # 获取最终状态
            if result["task_id"]:
                try:
                    from task_planner.core.database import get_task
                    task_data = await asyncio.to_thread(get_task, result["task_id"])
                    if task_data:
                        result["final_state"] = task_data
                except Exception as e:
                    logger.warning(f"Failed to fetch task final state: {e}")

            result["success"] = result["error"] is None

        except asyncio.TimeoutError:
            result["error"] = f"Timeout after {self.timeout}s"
            await self.cancel()
        except Exception as e:
            result["error"] = str(e)
            logger.exception(f"Harness run failed: {e}")
        finally:
            result["elapsed"] = time.time() - start_time
            # 清理流式状态
            if self.thread_id:
                try:
                    await cancel_stream(self.thread_id)
                except:
                    pass

        return result

    async def cancel(self):
        if self.thread_id:
            await cancel_stream(self.thread_id)
            self._cancelled = True

    @staticmethod
    async def get_checkpoint_state(thread_id: str) -> Optional[Dict[str, Any]]:
        """获取 checkpoint 状态（用于断点续传测试）"""
        from task_planner.core.graph.workflow import get_thread_state_async
        try:
            return await get_thread_state_async(thread_id)
        except:
            return None

    @staticmethod
    async def resume(thread_id: str, user_action: str = "continue", modified_input: str = None):
        """恢复执行（测试用）"""
        from task_planner.services.agent import resume_task, modify_task
        if user_action == "continue":
            return await resume_task(thread_id)
        elif user_action == "modify" and modified_input:
            return await modify_task(thread_id, modified_input)
        return False
