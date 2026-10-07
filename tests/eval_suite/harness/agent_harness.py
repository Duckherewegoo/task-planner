"""
Agent 运行时封装：异步调用 task_planner 的流式执行，收集输出

Changelog:
  ✅ P0-1：用 asyncio.timeout 显式包裹流消费，防止 case 卡死拖垮整个评估批次；
           Python 3.10 用等价上下文管理器兜底。
  ✅ P0-2：set_req_id() 不传参（让它自己生成），thread_id 独立生成；
           两者语义分离，日志里能区分"会话"与"单次请求"。
  ✅ P1-1：移除对 stream_manager 的依赖，直接复用 agent.cancel_task，
           避免 stream_manager 未初始化或循环导入问题。
  ✅ P1-2：get_task 改用 async 调用；如果不存在则跳过 final_state 采集。
  ✅ P1-3：所有 `except:` 改为 `except Exception:`，不再吞 KeyboardInterrupt。
  ✅ P2-x：output 字段语义明确（= direct_response）；
           success 语义增强（无 error 且确实产出结果）；
           needs_planning 判断简化；日志全部 %s 风格。
"""

import asyncio
import sys
import time
import uuid
from typing import (
    Any,
    Optional,
)

from task_planner.infrastructure.logger_setup import (
    get_logger,
    set_req_id,
)
from task_planner.services.agent import (
    cancel_task,
    run_task_stream,
)

logger = get_logger("eval.agent_harness")


# ✅ P0-1 修复：兼容 Python 3.10（asyncio.timeout 是 3.11+）
if sys.version_info >= (3, 11):
    from asyncio import timeout as _asyncio_timeout
else:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _asyncio_timeout(seconds: float):
        """
        Python 3.10 的 asyncio.timeout 替代实现。

        原理：用 loop.call_later 定时 cancel 当前 task；
              区分"我们主动取消的"与"外部取消的"。
        """
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        handle = loop.call_later(seconds, task.cancel)
        try:
            yield
        except asyncio.CancelledError:
            if handle.cancelled():
                # 是超时触发的 cancel，转成 TimeoutError
                raise TimeoutError() from None
            # 是外部取消，原样抛出
            raise
        finally:
            handle.cancel()


class AgentHarness:
    """
    封装 Agent 运行，捕获执行结果、中间状态、延迟等。

    典型用法：
        harness = AgentHarness(enable_refine=True, timeout=300)
        result = await harness.run("帮我做番茄炒蛋")
    """

    def __init__(self, enable_refine: bool = True, timeout: int = 300):
        self.enable_refine = enable_refine
        self.timeout = timeout
        self.thread_id: str | None = None

    async def run(self, user_input: str) -> dict[str, Any]:
        """
        执行单个测试用例，返回完整结果字典。

        Returns:
            {
                "input": str,              # 原始输入
                "thread_id": str,          # 会话 ID
                "output": str,             # 用户可见输出（= direct_response）
                "error": str | None,       # 错误信息
                "snapshots": list[dict],   # 全部中间快照（用于回放）
                "final_state": dict,       # 从 DB 拉取的最终状态（可选）
                "nodes": list[dict],
                "edges": list[dict],
                "task_id": str,
                "elapsed": float,          # 秒
                "needs_planning": bool,
                "direct_response": str,
                "success": bool,
            }
        """
        # ✅ P0-2 修复：thread_id 与 req_id 独立生成
        #    - thread_id: 会话标识（用于 checkpoint / session store）
        #    - req_id:    单次请求追踪（用于日志串联）
        self.thread_id = str(uuid.uuid4())
        rid = set_req_id()
        logger.info(
            "[Harness] thread=%s req=%s input=%s",
            self.thread_id, rid, user_input[:50],
        )

        result: dict[str, Any] = {
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
            # ✅ P0-1 修复：显式超时包裹流消费，防止永久 hang
            async with _asyncio_timeout(self.timeout):
                async for snapshot in run_task_stream(
                    user_input=user_input,
                    thread_id=self.thread_id,
                    enable_refine=self.enable_refine,
                    resume=False,
                ):
                    result["snapshots"].append(snapshot)

                    # ── 提取关键字段（后到的快照会覆盖先到的）──
                    if snapshot.get("direct_response"):
                        result["direct_response"] = snapshot["direct_response"]
                        # ✅ P2 修复：output 语义明确 = 用户可见的输出
                        result["output"] = snapshot["direct_response"]

                    if snapshot.get("nodes"):
                        result["nodes"] = snapshot["nodes"]
                        result["edges"] = snapshot.get("edges", [])

                    if snapshot.get("task_id"):
                        result["task_id"] = snapshot["task_id"]

                    # ✅ P2 修复：bool 字段直接取，简化判断
                    if "needs_planning" in snapshot:
                        result["needs_planning"] = bool(snapshot["needs_planning"])

                    # ── 终止信号：出现即跳出 ──
                    if snapshot.get("type") in ("complete", "cancelled", "error", "timeout"):
                        result["error"] = (
                            snapshot.get("error") or snapshot.get("message")
                        )
                        break

            # ── 采集 final_state（可选，失败不影响主结果）──
                if result["task_id"] and not result["task_id"].startswith("local-"):
                    try:
                        from task_planner.core.db import get_task
                        # ✅ P1-2 修复：直接 await（若 get_task 是 async）
                        #    若你的 get_task 是同步函数，把下面这行换成：
                        #    task_data = await asyncio.to_thread(get_task, result["task_id"])
                        task_data = await get_task(result["task_id"])
                        if task_data:
                            result["final_state"] = task_data
                    except ImportError:
                        logger.debug("[Harness] get_task 不存在，跳过 final_state")
                    except Exception as e:
                        logger.warning("[Harness] 获取 final_state 失败: %s", e)

            # ✅ P2 修复：success 语义增强——无 error 且确实产出了结果
            has_output = bool(result["direct_response"]) or bool(result["nodes"])
            result["success"] = result["error"] is None and has_output

        except TimeoutError:
            result["error"] = f"Timeout after {self.timeout}s"
            logger.warning("[Harness] 超时 | thread=%s elapsed=%ds",
                           self.thread_id, self.timeout)
            # 尝试通知 Agent 取消，避免后台残留
            await self.cancel()

        except asyncio.CancelledError:
            result["error"] = "Cancelled"
            logger.warning("[Harness] 被外部取消 | thread=%s", self.thread_id)
            raise

        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            logger.exception("[Harness] run failed | thread=%s", self.thread_id)

        finally:
            result["elapsed"] = time.time() - start_time
            # ✅ P1-3 修复：用 except Exception 而非裸 except
            if self.thread_id:
                try:
                    await cancel_task(self.thread_id)
                except Exception as e:
                    logger.debug("[Harness] cleanup failed: %s", e)

        return result

    async def cancel(self) -> None:
        """显式取消当前 harness 持有的会话"""
        if self.thread_id:
            try:
                await cancel_task(self.thread_id)
            except Exception as e:
                logger.debug("[Harness] cancel failed: %s", e)

    @staticmethod
    async def get_checkpoint_state(thread_id: str) -> dict[str, Any] | None:
        """
        获取 checkpoint 状态（用于断点续传测试）。

        Returns:
            {"thread_id": str, "state": dict, "next_nodes": list, "has_checkpoint": bool}
            或 None（无 checkpoint / 查询失败）
        """
        from task_planner.core.graph.workflow import get_thread_state_async
        try:
            return await get_thread_state_async(thread_id)
        except Exception as e:
            # ✅ P1-3 修复：不再裸 except
            logger.debug("[Harness] get_checkpoint_state failed: %s", e)
            return None

    @staticmethod
    async def resume(
        thread_id: str,
        user_action: str = "continue",
        modified_input: str | None = None,
    ) -> bool:
        """
        恢复执行（测试用）。

        Args:
            thread_id: 目标会话
            user_action: "continue" | "modify"
            modified_input: user_action="modify" 时必填

        Returns:
            True 表示成功调用，False 表示参数非法或任务不存在
        """
        from task_planner.services.agent import (
            modify_task,
            resume_task,
        )
        if user_action == "continue":
            return await resume_task(thread_id)
        elif user_action == "modify" and modified_input:
            return await modify_task(thread_id, modified_input)
        logger.warning(
            "[Harness] resume: 非法参数 action=%s modified_input=%s",
            user_action, bool(modified_input),
        )
        return False
