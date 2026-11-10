"""
llm_judge.py — 单个 LLM 评委评分。

设计依据（Splunk 指南）：
    - 用 Chain-of-Thought：先写推理，再给结论
    - 布尔判断比数字评分更可靠
    - 用不同模型家族减少自我偏好偏差
"""

import json

from task_planner.infrastructure.llm import async_call_llm, extract_json
from task_planner.infrastructure.logger_setup import get_logger

from ..types import AgentTrace, DimensionScore, GoldenCase, JudgeScore

logger = get_logger("task_planner.eval_suite.llm_judge")

_EXPECTED_DIMENSIONS = (
    "task_understanding",
    "node_quality",
    "edge_quality",
    "executability",
    "structural_integrity",
)


_JUDGE_PROMPT = """你是任务规划系统的专业评委。请严格根据以下标准评分。

## 评分维度（每项 0-10 分）
1. **task_understanding** — category 和 needs_planning 是否正确
2. **node_quality** — 节点原子化、可验证、无冗余无缺失
3. **edge_quality** — 依赖关系准确，并行/串行安排合理
4. **executability** — 节点含具体参数、标准，可直接执行
5. **structural_integrity** — DAG 无环、ID 连续、边有效

## 用户需求
{user_input}

## 参考规划（黄金标准）
{expected_plan}

## Agent 实际生成
{actual_plan}

## 输出要求
先对每个维度写一句推理，再给分数。
严格按以下 JSON 格式输出（不要 markdown 围栏）：
{{
  "dimensions": [
    {{"name": "task_understanding", "score": 0-10, "reason": "..."}},
    {{"name": "node_quality", "score": 0-10, "reason": "..."}},
    {{"name": "edge_quality", "score": 0-10, "reason": "..."}},
    {{"name": "executability", "score": 0-10, "reason": "..."}},
    {{"name": "structural_integrity", "score": 0-10, "reason": "..."}}
  ],
  "overall_comment": "一句话总体评价"
}}"""


async def llm_judge(
    trace: AgentTrace,
    golden: GoldenCase,
    judge_model: str,
    *,
    req_id: str = "eval_judge",
) -> JudgeScore:
    """用单个 LLM 模型评分。"""
    if trace.error:
        return JudgeScore(
            judge_model=judge_model,
            error=f"Agent 执行失败: {trace.error}",
        )

    prompt = _JUDGE_PROMPT.format(
        user_input=golden.user_input,
        expected_plan=json.dumps(
            golden.expected_plan,
            ensure_ascii=False,
            indent=2,
        ),
        actual_plan=json.dumps(
            trace.plan,
            ensure_ascii=False,
            indent=2,
        ),
    )

    try:
        raw = await async_call_llm(
            model=judge_model,
            prompt=prompt,
            enable_thinking=False,
            req_id=req_id,
        )
        parsed = extract_json(raw)
        if not parsed:
            return JudgeScore(
                judge_model=judge_model,
                error="评委返回无法解析为 JSON",
                raw_response=raw[:500],
            )

        dims_list: list[DimensionScore] = []
        for d in parsed.get("dimensions", []):
            if not isinstance(d, dict) or "name" not in d:
                continue
            try:
                score = float(d.get("score", 0))
            except TypeError, ValueError:
                logger.warning("[Judge] %s score 非法: %r", judge_model, d.get("score"))
                score = 0.0
            # 限幅到 [0, 10]，防止评委返回越界分数污染聚合
            score = max(0.0, min(10.0, score))
            dims_list.append(
                DimensionScore(
                    name=d["name"],
                    score=max(0.0, min(10.0, score)),
                    reason=str(d.get("reason", "")),
                )
            )
        dims = tuple(dims_list)
        missing = set(_EXPECTED_DIMENSIONS) - {d.name for d in dims}
        if missing:
            logger.warning("[Judge] %s 缺维度: %s", judge_model, sorted(missing))

        return JudgeScore(
            judge_model=judge_model,
            dimensions=dims,
            overall_comment=parsed.get("overall_comment", ""),
            raw_response=raw[:500],
        )
    except Exception as e:
        logger.exception("[Judge] %s 评分失败", judge_model)
        return JudgeScore(
            judge_model=judge_model,
            error=f"评委调用失败: {e}",
        )
