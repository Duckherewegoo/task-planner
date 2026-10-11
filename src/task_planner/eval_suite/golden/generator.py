"""
generator.py — 用 LLM 合成黄金标准数据集。

设计依据（OrchDAG）：
    合成数据生成流水线，将工具执行建模为 DAG，复杂度可控。
    不依赖人工标注，用程序化规则验证结构有效性。
"""
from __future__ import annotations
import asyncio
import hashlib

import json
from pathlib import Path

from task_planner.infrastructure.llm import async_call_llm, extract_json
from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

from ..types import Difficulty, GoldenCase
from .validator import validate_golden_plan

logger = get_logger("task_planner.eval_suite.golden")

# 防御常量
_MAX_INPUT_LEN = 500           # user_input 截断长度
_RETRY_BACKOFF_SEC = 1.5       # 重试指数退避基数

# ── 生成提示模板（按难度）──
_GEN_PROMPT = """你是任务规划专家。请为以下用户需求生成一个参考 DAG 规划。

## 用户需求
{user_input}

## 难度要求
{difficulty_hint}

## 输出格式（严格 JSON）
{{
  "task_name": "≤20字",
  "nodes": [{{"id": 1, "name": "动词+宾语(≤15字)", "detail": "具体操作含参数"}}],
  "edges": [{{"from": 1, "to": 2, "label": "可选"}}]
}}

## 硬性约束
- id 从 1 开始连续编号
- nodes 不可为空
- edges 中 from/to 必须引用已存在的 id
- 无自环
- 无循环依赖"""

_DIFFICULTY_HINTS = {
    Difficulty.SIMPLE: "生成 3-5 个节点的线性或简单分支 DAG",
    Difficulty.MEDIUM: "生成 5-10 个节点，包含至少一组并行分支",
    Difficulty.COMPLEX: "生成 10-20 个节点，包含并行、汇聚、条件分支",
}


async def generate_golden_case(
    user_input: str,
    *,
    difficulty: Difficulty = Difficulty.MEDIUM,
    model: str | None = None,
    case_id: str | None = None,
    tags: tuple[str, ...] = (),
    max_retries: int = 3,
) -> GoldenCase | None:
    """
    用 LLM 生成一条黄金标准用例。

    生成后自动程序化验证结构。验证失败则重试（最多 max_retries 次）。
    """
    model = model or _hub.dev.LLM_PLANNER_MODEL
    case_id = case_id or f"auto_{hashlib.md5(user_input.encode()).hexdigest()[:6]}"

    # 长度保护（超长输入会撑爆 prompt）
    if len(user_input) > _MAX_INPUT_LEN:
        logger.warning(
            "[Golden] user_input 超长 (%d > %d)，已截断",
            len(user_input), _MAX_INPUT_LEN,
        )
        user_input = user_input[:_MAX_INPUT_LEN]


    prompt = _GEN_PROMPT.format(
        user_input=user_input,
        difficulty_hint=_DIFFICULTY_HINTS[difficulty],
    )

    for attempt in range(1, max_retries + 1):
        try:
            raw = await async_call_llm(
                model=model, prompt=prompt,
                enable_thinking=False,
                req_id=f"golden_gen_{attempt}",
            )
            parsed = extract_json(raw)
            if not parsed:
                logger.warning("[Golden] 第 %d 次生成返回非 JSON", attempt)
                continue

            case = GoldenCase(
                case_id=case_id,
                user_input=user_input,
                expected_plan={
                    "nodes": parsed.get("nodes", []),
                    "edges": parsed.get("edges", []),
                },
                expected_intent={
                    "needs_planning": True,
                    "category": tags[0] if tags else "other",
                    "complexity": difficulty.value,
                },
                tags=tags,
                difficulty=difficulty,
                generated_by=model,
                validated=False,
            )

            is_valid, reason = validate_golden_plan(case.expected_plan)
            if is_valid:
                logger.info(
                    "[Golden] ✅ %s 生成成功 (nodes=%d, edges=%d)",
                    case_id, len(case.expected_plan["nodes"]),
                    len(case.expected_plan["edges"]),
                )
                return GoldenCase(
                    case_id=case.case_id,
                    user_input=case.user_input,
                    expected_plan=case.expected_plan,
                    expected_intent=case.expected_intent,
                    tags=case.tags,
                    difficulty=case.difficulty,
                    generated_by=case.generated_by,
                    validated=True,
                )
            else:
                logger.warning(
                    "[Golden] 第 %d 次生成结构无效: %s", attempt, reason,
                )
        except Exception as e:
            logger.error("[Golden] 第 %d 次生成异常: %s", attempt, e)

        # 指数退避（最后一次不等待）
        if attempt < max_retries:
            await asyncio.sleep(_RETRY_BACKOFF_SEC ** (attempt - 1))

    logger.error("[Golden] %s 生成失败（重试 %d 次）", case_id, max_retries)
    return None


async def generate_dataset(
    specs: list[dict],
    output_dir: Path,
    *,
    model: str | None = None,
) -> list[GoldenCase]:
    """
    批量生成黄金标准数据集。

    Args:
        specs: [{user_input, difficulty, tags}, ...]
        output_dir: 保存 JSON 的目录
    """
    cases: list[GoldenCase] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, spec in enumerate(specs):
        case = await generate_golden_case(
            spec["user_input"],
            difficulty=Difficulty(spec.get("difficulty", "medium")),
            model=model,
            case_id=f"golden_{i:03d}",
            tags=tuple(spec.get("tags", [])),
        )
        if case:
            cases.append(case)
            path = output_dir / f"{case.case_id}.json"
            path.write_text(
                json.dumps({
                    "case_id": case.case_id,
                    "user_input": case.user_input,
                    "expected_plan": case.expected_plan,
                    "expected_intent": case.expected_intent,
                    "tags": list(case.tags),
                    "difficulty": case.difficulty.value,
                    "generated_by": case.generated_by,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    if len(cases) < len(specs):
        logger.warning(
            "[Golden] 数据集生成完成: %d/%d 成功，失败 %d 条",
            len(cases), len(specs), len(specs) - len(cases),
        )
    else:
        logger.info("[Golden] 数据集生成完成: %d/%d 全部成功", len(cases), len(specs))
    return cases
