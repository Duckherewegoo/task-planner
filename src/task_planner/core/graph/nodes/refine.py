"""refine.py — 节点细化（异步并发）

设计说明：
  本节点接收 plan_node 产出的 nodes 列表，并发调用 LLM 细化每个节点，
  返回 refined 后的节点列表。

  约定：
    - 单节点失败不阻塞其他节点（降级为 fallback dict）
    - 取消时尽量少提交任务，已提交的让 LLM 层感知 cancel_event
    - 输出 refined_nodes 与 nodes 内容一致但为独立副本

⚠️ 关于 asyncio.CancelledError：
   它是 BaseException 子类（Python 3.8+），不是 Exception 子类。
   本文件**不捕获**它——必须让它冒泡，协程调度层才能正确识别取消。
   业务层的取消走 cancel_event.is_set() 检查（本文件已实现）。
   两者不能混为一谈——把 CancelledError 当成"业务异常"捕获会导致
   取消无法传播，且会污染 gather 的 results 列表（见下方 results 过滤）。

⚠️ 关于 Semaphore：
   模块级 Semaphore 会跨 event loop 复用——Python 3.10+ 的
   _LoopBoundMixin 首次 await 时绑定 loop，第二次从新 loop 使用抛
   "is bound to a different event loop"。生产单 loop 环境碰不到，
   但测试（pytest-asyncio 每用例新 loop）和 CLI 会炸。
   所以本文件每次 refine_node 调用创建新 Semaphore——绑定当前 loop。

⚠️ 层边界约定：
   本节点**只防御自己的 LLM 边界**（refine_node 返回值）。
   上游写入 state 的字段（state["intent"] 等）由写入方负责保证类型。
   这不是"信任上游"，而是"每个节点守好自己的边界"。
   state["intent"] 的 dict 保证在 intent_node 里做（见跨文件待办）。
"""

import asyncio
import os
from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.llm import refine_node as refine_node_llm
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

from task_planner.utils.context import get_cancel_event

logger = get_logger(__name__)

# 默认并发上限。真实值由 _parse_concurrency() 从环境变量解析。
# 默认 10 是保守值——不会打爆主流 LLM API，也能让大 DAG 受益。
_REFINE_CONCURRENCY_DEFAULT = 10


def _parse_concurrency() -> int:
    """
    解析 REFINE_CONCURRENCY；非法值回退默认，不炸启动。

    三种非法值：
      - "abc"：int() 抛 ValueError → 若直接 int() 会让模块 import 失败
      - "-1"：asyncio.Semaphore(-1) 抛 ValueError → 同上
      - "0"：asyncio.Semaphore(0) 永久阻塞 → 不报错、不崩，图静静卡在 refine
              （最阴险——运维会误以为 LLM 慢，不会想到环境变量配错）

    处理哲学：配置错误时"能跑"比"不跑"好，尤其这只是性能调优参数，
    不是功能开关。但要留痕（warning 日志）。
    """
    raw = os.getenv("REFINE_CONCURRENCY", str(_REFINE_CONCURRENCY_DEFAULT))
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "[Refine] REFINE_CONCURRENCY=%r 不是整数，回退 %d",
            raw, _REFINE_CONCURRENCY_DEFAULT,
        )
        return _REFINE_CONCURRENCY_DEFAULT
    if n < 1:
        logger.warning(
            "[Refine] REFINE_CONCURRENCY=%d < 1，回退 %d",
            n, _REFINE_CONCURRENCY_DEFAULT,
        )
        return _REFINE_CONCURRENCY_DEFAULT
    return n


_REFINE_CONCURRENCY = _parse_concurrency()


async def refine_node(state: TaskState, config: RunnableConfig) -> dict[str, Any]:
    """
    节点细化（异步并发）。

    契约：
      - 输入：state["nodes"] / state["user_input"] / state["intent"]
      - 输出：
          * refined_nodes：细化后的节点列表（独立对象）
          * nodes：与 refined_nodes 内容一致的**独立副本**
            （避免下游就地修改污染另一个字段）
          * steps：追加一条"节点细化完成"
      - 单节点失败：降级为 fallback dict，details 记录原因，不阻塞其他节点
      - 取消：cancel_event 设置后不再提交新任务；已提交的由 LLM 层感知

    取消语义（两段式）：
      1. cancel_event.is_set() —— 业务层取消信号，本文件在各检查点读取。
         用于"优雅取消"：不再提交新任务、未提交的节点补占位记录。
      2. asyncio.CancelledError —— 协程调度层取消信号，本文件**不捕获**，
         让它冒泡，由调用方决定如何处理。
         在 gather(return_exceptions=True) 场景下，若某个子任务被外部取消，
         CancelledError 会作为"结果"出现在 results 列表里——下方用
         `isinstance(res, BaseException)` 精确过滤，防止它被当成正常节点。

    异常分层：
      - 预期内失败（RuntimeError / TimeoutError）：在 `_refine_one` 内捕获，
        就地降级写 details，不中断并发批次。
      - 其他异常（网络、解析、未知类型）：不在 `_refine_one` 内捕获，
        让它冒泡到 gather，由 results 过滤统一处理——避免此处漏掉某类异常。

    层边界：
      本节点防御的是**自己的 LLM 边界**（refine_node 返回值可能非 dict）。
      上游写入 state 的字段（state["intent"] 的类型）由写入方保证——
      intent_node 负责 recognize_intent 返回值的契约（见跨文件待办）。
    """
    rid = set_req_id()
    logger.info("[Graph] refine_node (req=%s)", rid)

    nodes_raw = state["nodes"]
    intent = state["intent"]
    cancel_event = get_cancel_event()

    # ✅ Semaphore 绑定当前 event loop：
    #    模块级创建会在首次 await 时绑定 loop，跨 loop 复用抛 RuntimeError。
    #    每次 refine_node 调用创建一个——绑定当前 loop，服务于本次所有任务。
    #    注意粒度：不是每个 _refine_one 创建（那会让并发上限失效），
    #              而是每次 refine_node 创建（本次内所有任务共享同一个）。
    semaphore = asyncio.Semaphore(_REFINE_CONCURRENCY)

    async def _refine_one(n: dict[str, Any]) -> dict[str, Any]:
        """
        细化单个节点。

        入参 n 是浅拷贝（由调用方 `dict(n)` 保证）——就地修改后返回，
        不影响 nodes_raw 中的原始元素。

        ⚠️ 取消检查分两层：
          - 信号量外：快路径。任务在排队等信号量时能立即感知取消并返回，
            不必等前面任务跑完（取消响应延迟从"等 N 个任务"降到"立即"）。
          - 信号量内：安全网。从"拿到信号量"到"调用 LLM"之间取消可能发生，
            内层检查覆盖这段窗口。

        ⚠️ 不捕获 asyncio.CancelledError：
           - 它继承自 BaseException，代表"任务被调度层取消"
           - 让它冒泡后，gather(return_exceptions=True) 会把它作为结果返回，
             由外层 results 过滤识别并处理
           - 业务层取消走 cancel_event 检查，语义独立
        """
        # ✅ 快路径：排队等信号量前先检查取消
        if cancel_event and cancel_event.is_set():
            logger.info("[Graph] 节点%s细化跳过(已取消, req=%s)", n.get("id"), rid)
            n["details"] = "⏹️ 用户取消"
            return n

        async with semaphore:  # ✅ 并发上限（当前 loop 绑定）
            # ✅ 安全网：拿到信号量后再检查一次，覆盖排队窗口内发生的取消
            if cancel_event and cancel_event.is_set():
                logger.info("[Graph] 节点%s细化跳过(已取消-信号量内, req=%s)", n.get("id"), rid)
                n["details"] = "⏹️ 用户取消"
                return n
            try:
                r = await refine_node_llm(
                    n,
                    state["user_input"],
                    str(intent.get("category", "other")),
                    req_id=rid,
                    cancel_event=cancel_event,
                )
                # ✅ 防御自己的 LLM 边界：refine_node 返回值不信任——
                # LLM 内部工具调用失败可能返回 None / 非 dict。
                # 类型声明 dict[str, Any] 不等于运行时保证。
                if not isinstance(r, dict):
                    logger.warning(
                        "[Graph] 节点%s refine_node 返回非 dict: %s (req=%s)",
                        n.get("id"), type(r).__name__, rid,
                    )
                    n["details"] = f"细化异常: 返回 {type(r).__name__}"
                    return n
                n["name"] = r.get("name", n.get("name", ""))
                n["details"] = r.get("details", r.get("detail", ""))
                n["meta"] = r.get("meta", {})
            except (RuntimeError, TimeoutError) as e:
                # 预期内的单节点失败：降级记录，不中断其他节点。
                # 其他异常类型不在此捕获——让它们冒泡到 gather，
                # 由 results 过滤统一处理，避免此处漏掉某类异常。
                logger.warning("[Graph] 节点%s细化失败: %s (req=%s)", n.get("id"), e, rid)
                n["details"] = f"细化失败: {e}"
            return n

    tasks = []
    for n in nodes_raw:
        if cancel_event and cancel_event.is_set():
            logger.info(
                "[Graph] refine 提前终止，已提交 %d/%d (req=%s)",
                len(tasks), len(nodes_raw), rid,
            )
            break

        tasks.append(_refine_one(dict(n)))
        # ✅ 让出控制权：给事件循环一个调度点，让外部取消信号
        #    （session.cancel_event.set() 由 commands.cancel_task 触发）
        #    有机会在循环迭代之间被感知。
        #    不加这行：主循环是同步的——1000 个协程对象瞬间创建完毕，
        #    中途任何外部 set 都无法被 break 检测到。
        #    加了之后：每次迭代让出 → 外部任务可运行 → set →
        #    下一轮检测到 → break。
        #    代价：每节点一次事件循环调度（微秒级）。
        await asyncio.sleep(0)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    refined: list[dict[str, Any]] = []
    for i, res in enumerate(results):
        # ✅ 用 BaseException 而非 Exception：
        #    - gather(return_exceptions=True) 会捕获所有异常，包括 BaseException
        #      子类（如 asyncio.CancelledError、KeyboardInterrupt）
        #    - 用 isinstance(res, Exception) 会漏掉这些，把它们当成"正常结果"
        #      塞进 refined
        #    - 后果：refined 里混入非 dict 对象，下游 node.get("id") 会抛
        #      AttributeError——一个和"取消"毫无关系的报错
        if isinstance(res, BaseException):
            fallback = dict(nodes_raw[i])
            fallback["details"] = f"细化异常: {res}"
            logger.warning(
                "[Graph] 节点%s细化异常: %s (req=%s)",
                fallback.get("id"), res, rid,
            )
            refined.append(fallback)
        elif isinstance(res, dict):
            # ✅ 契约检查：_refine_one 类型上保证返回 dict，
            # 但类型声明 != 运行时保证。这里显式确认，让 refined 的
            # list[dict] 类型标注在运行时也成立。
            refined.append(res)
        else:
            # 理论上不可达——_refine_one 的所有返回路径都返回 dict。
            # 保底防御：不把非 dict 塞进 refined 破坏下游契约。
            fallback = dict(nodes_raw[i])
            fallback["details"] = f"细化返回非 dict: {type(res).__name__}"
            logger.warning(
                "[Graph] 节点%s _refine_one 返回非 dict: %s (req=%s)",
                fallback.get("id"), type(res).__name__, rid,
            )
            refined.append(fallback)

    # 未提交的节点（提前 break）：降级为"未提交"占位
    if cancel_event and cancel_event.is_set():
        for i in range(len(refined), len(nodes_raw)):
            fallback = dict(nodes_raw[i])
            fallback["details"] = "⏹️ 用户取消(未提交)"
            refined.append(fallback)

    return {
        "refined_nodes": refined,
        # ✅ 独立副本：避免下游对 nodes 的就地修改污染 refined_nodes（或反之）。
        # 两个字段共享引用会退化成一个字段的语义，且这个隐式契约未在
        # 代码里声明——未来下游若做了 append/pop/sort，refined_nodes 会跟着变。
        "nodes": list(refined),
        "steps": state.get("steps", []) + ["节点细化完成"],
    }
