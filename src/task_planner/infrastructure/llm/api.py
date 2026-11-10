"""api.py — 5 个业务接口（意图 / 规划 / 细化 / 执行 / 直答）"""

import asyncio
import json
from typing import Any

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.constants import MOCK_RESPONSE_PREFIX
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.prompts.loader import (
    INTENT_PROMPT,
    NODE_REFINE_PROMPT,
    PLANNER_PROMPT,
    render_template,
)
from task_planner.infrastructure.regexes import SKIP_PLANNING_PATTERNS

from .client import get_semaphore
from .core import async_call_llm
from .errors import LLMCancelledError, LLMClientError, LLMResponseError, LLMTimeoutError
from .json_utils import extract_json, normalize_llm_output
from .mocks import mock_intent, mock_plan, mock_refine
from .validator import validate_intent

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
# 直接对话
# ═══════════════════════════════════════════════════════════════
async def direct_chat(
    user_input: str,
    req_id: str,
    cancel_event: asyncio.Event | None = None,
    timeout: float | None = None,
) -> str:
    """直接对话接口（异步）"""
    if _hub.dev.USE_MOCK_LLM:
        return f"{MOCK_RESPONSE_PREFIX}{user_input}"
    async with get_semaphore():
        return await async_call_llm(
            model=_hub.dev.LLM_INTENT_MODEL,
            prompt=user_input,
            enable_thinking=False,
            req_id=req_id,
            cancel_event=cancel_event,
            timeout=timeout,
        )


# ═══════════════════════════════════════════════════════════════
# 意图识别
# ═══════════════════════════════════════════════════════════════
async def recognize_intent(
    user_input: str,
    req_id: str,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """意图识别异步主入口"""
    # 空输入短路
    if not user_input or not user_input.strip():
        return {
            "needs_planning": False,
            "category": "other",
            "summary": "用户输入为空",
            "complexity": "simple",
        }

    # 正则硬拦截
    for pattern in SKIP_PLANNING_PATTERNS:
        if pattern.search(user_input):
            logger.info("[Intent] 显式拒绝规划命中 (req=%s): %s", req_id, pattern.pattern)
            return {
                "needs_planning": False,
                "category": "consultation",
                "summary": "用户显式拒绝规划",
                "complexity": "simple",
            }

    if _hub.dev.USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", req_id)
        return mock_intent(req_id)

    summary = "意图识别异常"
    try:
        prompt = render_template(INTENT_PROMPT, req_id, user_input=user_input)
        raw = await async_call_llm(
            model=_hub.dev.LLM_INTENT_MODEL,
            prompt=prompt,
            enable_thinking=_hub.dev.LLM_INTENT_ENABLE_THINKING,
            req_id=req_id,
            cancel_event=cancel_event,
        )
        intent = extract_json(raw)
        if not intent or not isinstance(intent, dict):
            raise LLMResponseError("JSON解析为空或非dict", req_id)

        intent = validate_intent(intent, user_input)
        intent.setdefault("needs_planning", False)
        intent.setdefault("category", "other")
        intent.setdefault("summary", "任务解析完成")
        intent.setdefault("complexity", "simple")

        # Post-check 二次拦截
        if intent.get("needs_planning") is True:
            for pattern in SKIP_PLANNING_PATTERNS:
                if pattern.search(user_input):
                    logger.warning("[Intent] LLM误判planning=True，正则二次拦截 (req=%s)", req_id)
                    intent["needs_planning"] = False
                    intent["category"] = "consultation"
                    intent["summary"] = "用户显式拒绝规划(LLM纠偏)"
                    break
            intent = validate_intent(intent, user_input)
        return intent

    except LLMCancelledError:
        logger.warning("[LLMClient] 意图识别被取消 (req=%s)", req_id)
        raise
    except LLMTimeoutError:
        logger.error("[LLMClient] 意图识别超时 (req=%s)", req_id)
        summary = "意图识别超时"
    except LLMResponseError as e:
        logger.error("[LLMClient] 意图解析格式错误: %s (req=%s)", e, req_id)
        summary = "意图解析格式错误"
    except LLMClientError as e:
        logger.error("[LLMClient] 意图识别服务异常: %s (req=%s)", e, req_id)
        summary = "意图识别服务异常"
    except Exception as e:
        logger.exception("[LLMClient] 意图识别未知异常: %s (req=%s)", e, req_id)
        summary = "意图识别异常"

    return {
        "needs_planning": False,
        "category": "other",
        "summary": summary,
        "complexity": "simple",
    }


# ═══════════════════════════════════════════════════════════════
# 规划
# ═══════════════════════════════════════════════════════════════
async def generate_plan(
    user_input: str,
    intent_info: dict[str, Any],
    req_id: str,
    cancel_event: asyncio.Event | None = None,
    few_shot_text: str = "",
) -> dict[str, Any]:
    """异步生成执行计划。

    Args:
        user_input:    用户原始输入
        intent_info:   意图识别输出（category / summary 等）
        req_id:        请求 ID
        cancel_event:  取消信号，透传给 LLM 层
        few_shot_text: retrieve 节点产出的 few-shot 案例文本。空字符串表示
                       不注入（retrieve 未启用 / 无命中 / plan_node 上游异常）。
                       非空时在基础 prompt 之后追加"相似历史案例"段落。

    Returns:
        plan dict，包含 task_name / description / nodes / edges。
        失败时返回占位 dict（task_name="规划失败"），不抛异常。
    """
    if not isinstance(intent_info, dict):
        logger.error(
            "[Planner] intent_info 类型非法: %s (req=%s)",
            type(intent_info).__name__,
            req_id,
        )
        return {
            "task_name": "规划失败",
            "description": "intent_info 参数类型非法",
            "nodes": [],
            "edges": [],
        }

    if _hub.dev.USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", req_id)
        if few_shot_text:
            logger.info(
                "[Planner] Mock 模式忽略 few-shot（%d 字符） (req=%s)",
                len(few_shot_text),
                req_id,
            )
        return mock_plan(req_id)

    error_msg = "规划生成异常"
    try:
        prompt = render_template(
            PLANNER_PROMPT,
            req_id,
            user_input=user_input,
            category=intent_info.get("category", "other"),
            summary=intent_info.get("summary", ""),
        )

        # ── few-shot 注入 ──
        # 不依赖 PLANNER_PROMPT 模板里的占位符；few_shot_text 为空时
        # prompt 与改动前逐字节一致。
        if few_shot_text:
            prompt = (
                prompt
                + "\n\n## 相似历史案例（few-shot 参考）\n"
                + "以下案例展示了类似任务的规划方式，请参考其节点粒度和结构，"
                + "但不要照搬，需根据当前需求灵活调整：\n\n"
                + few_shot_text
            )
            logger.info(
                "[Planner] 注入 few-shot（%d 字符） (req=%s)",
                len(few_shot_text),
                req_id,
            )

        raw = await async_call_llm(
            model=_hub.dev.LLM_PLANNER_MODEL,
            prompt=prompt,
            enable_thinking=_hub.dev.LLM_PLANNER_ENABLE_THINKING,
            req_id=req_id,
            cancel_event=cancel_event,
        )
        plan = extract_json(raw)
        if not plan or not isinstance(plan, dict):
            raise LLMResponseError("Plan JSON解析为空或非dict", req_id)

        plan.setdefault("task_name", "未命名任务")
        plan.setdefault("description", "")
        plan.setdefault("nodes", [])
        plan.setdefault("edges", [])
        return plan

    except LLMCancelledError:
        logger.warning("[Planner] 规划生成被取消 (req=%s)", req_id)
        raise
    except LLMTimeoutError:
        logger.error("[Planner] 规划生成超时 (req=%s)", req_id)
        error_msg = "规划生成超时"
    except LLMResponseError as e:
        logger.error("[Planner] 规划解析格式错误: %s (req=%s)", e, req_id)
        error_msg = f"规划解析格式错误: {e}"
    except LLMClientError as e:
        logger.error("[Planner] 规划生成服务异常: %s (req=%s)", e, req_id)
        error_msg = f"规划生成服务异常: {e}"
    except Exception as e:
        logger.exception("[Planner] 规划生成未知异常: %s (req=%s)", e, req_id)
        error_msg = "规划生成异常"

    return {
        "task_name": "规划失败",
        "description": f"[规划失败] {error_msg}",
        "nodes": [],
        "edges": [],
    }

# ═══════════════════════════════════════════════════════════════
# 节点细化
# ═══════════════════════════════════════════════════════════════
async def refine_node(
    node: dict[str, Any],
    user_input: str,
    category: str,
    req_id: str,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """异步细化单节点"""
    _REQUIRED = ("name", "details", "meta")

    def _fallback(error_msg: str, original_node: dict) -> dict[str, Any]:
        return {
            "name": original_node.get("name", "未命名节点"),
            "details": f"[细化失败] {error_msg}",
            "meta": {"refine_error": error_msg, "fallback": True},
        }

    if not isinstance(node, dict):
        logger.error("[Refine] node 类型非法: %s (req=%s)", type(node).__name__, req_id)
        return _fallback("node参数类型非法", {})

    if _hub.dev.USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", req_id)
        return mock_refine(req_id)

    try:
        prompt = render_template(
            NODE_REFINE_PROMPT,
            req_id,
            node_json=json.dumps(node, ensure_ascii=False),
            user_input=user_input,
            category=category,
        )
        raw = await async_call_llm(
            model=_hub.dev.LLM_NODE_MODEL,
            prompt=prompt,
            enable_thinking=_hub.dev.LLM_NODE_ENABLE_THINKING,
            req_id=req_id,
            timeout=_hub.dev.LLM_NODE_TIMEOUT,
            cancel_event=cancel_event,
        )
        result = extract_json(raw)
        result = normalize_llm_output(result, expected_keys=list(_REQUIRED))
        for key in _REQUIRED:
            if key not in result or result[key] is None:
                logger.warning(
                    "[Refine] 归一化后仍缺少字段 '%s'，使用原始节点值兜底 (req=%s)",
                    key,
                    req_id,
                )
                result[key] = node.get(key, "")
        return result

    except LLMCancelledError:
        logger.warning("[Refine] 节点细化被取消 (req=%s)", req_id)
        raise
    except LLMTimeoutError:
        logger.error("[Refine] 节点细化超时 (req=%s)", req_id)
        return _fallback("节点细化超时", node)
    except LLMResponseError as e:
        logger.error("[Refine] 节点细化解析格式错误: %s (req=%s)", e, req_id)
        return _fallback("节点细化解析格式错误", node)
    except LLMClientError as e:
        logger.error("[Refine] 节点细化服务异常: %s (req=%s)", e, req_id)
        return _fallback("节点细化服务异常", node)
    except Exception as e:
        logger.exception("[Refine] 节点细化未知异常: %s (req=%s)", e, req_id)
        return _fallback("节点细化异常", node)


# ═══════════════════════════════════════════════════════════════
# 单节点执行
# ═══════════════════════════════════════════════════════════════
async def execute_node_llm(
    prompt: str,
    req_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
    timeout: float | None = None,
) -> tuple[bool, str]:
    """执行节点 LLM 调用（返回 (success, detail)）"""
    rid = req_id or "unknown"

    if _hub.dev.USE_MOCK_LLM:
        if cancel_event and cancel_event.is_set():
            raise LLMCancelledError("Mock: cancelled by event", rid)
        return True, "Mock执行完成"

    try:
        async with get_semaphore():
            raw = await async_call_llm(
                model=_hub.dev.LLM_NODE_MODEL,
                prompt=prompt,
                enable_thinking=_hub.dev.LLM_NODE_ENABLE_THINKING,
                req_id=rid,
                timeout=timeout or _hub.dev.LLM_NODE_TIMEOUT,
                cancel_event=cancel_event,
            )

        parsed = extract_json(raw)
        if not parsed:
            logger.warning("[Executor] LLM返回无法解析为JSON (req=%s): %s", rid, raw[:200])
            return False, f"⚠️ 执行结果解析失败，原始输出: {raw[:300]}"

        success = bool(parsed.get("success", False))
        detail_text = str(parsed.get("detail", "无详细描述"))

        icon = "✅" if success else "❌"
        logger.info("[Executor] %s 执行完成 (req=%s)", icon, rid)
        return success, detail_text

    except LLMCancelledError:
        logger.info("[Executor] 执行被取消 (req=%s)", rid)
        return False, "⏹️ 执行被取消"
    except LLMTimeoutError as e:
        logger.warning("[Executor] ⏰ %s (req=%s)", e, rid)
        return False, "⏰ 执行超时，请稍后重试"
    except LLMClientError as e:
        logger.error("[Executor] 💥 %s (req=%s)", e, rid)
        return False, f"💥 执行服务异常: {str(e)[:200]}"
    except Exception as e:
        logger.exception("[Executor] 💥 未知异常 (req=%s)", rid)
        return False, f"💥 执行异常: {type(e).__name__}: {str(e)[:200]}"


__all__ = [
    "direct_chat",
    "recognize_intent",
    "generate_plan",
    "refine_node",
    "execute_node_llm",
]
