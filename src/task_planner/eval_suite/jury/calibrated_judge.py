"""
calibrated_judge.py — 多评委聚合。

参考 AgentEval 的 CalibratedJudge 设计：
    - 四种投票策略：Median（默认）/ Mean / Unanimous / Weighted
    - 一致性 = 100 - (StdDev / Mean × 100)
    - t 分布近似置信区间
    - 优雅降级：单个评委失败不影响整体
    - 并行执行 + 信号量限流

评委选择（行业共识）：
    用不同模型家族，不是同一模型跑三次。
    如果只有一个 API Key，退而求其次用不同 prompt 策略。
"""

import asyncio
import statistics
from dataclasses import dataclass

from task_planner.infrastructure.logger_setup import get_logger

from ..scorers.llm_judge import llm_judge
from ..types import (
    AgentTrace,
    GoldenCase,
    JudgeScore,
    JuryResult,
    VotingStrategy,
)

logger = get_logger("task_planner.eval_suite.jury")


@dataclass(frozen=True)
class JudgeConfig:
    """单个评委的配置。"""
    model: str
    name: str
    weight: float = 1.0    # 仅 Weighted 策略使用


class CalibratedJudge:
    """
    多评委聚合器。

    用法：
        judge = CalibratedJudge([
            JudgeConfig(model="qwen3.7-plus", name="planner"),
            JudgeConfig(model="qwen3.7-flash", name="fast"),
        ])
        result = await judge.evaluate(trace, golden)
    """

    def __init__(
        self,
        judges: list[JudgeConfig],
        *,
        strategy: VotingStrategy = VotingStrategy.MEDIAN,
        min_judges: int = 1,
        max_parallel: int = 3,
    ):
        self.judges = judges
        self.strategy = strategy
        self.min_judges = min_judges
        self.max_parallel = max_parallel
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_parallel)
        return self._semaphore

    async def evaluate(
        self,
        trace: AgentTrace,
        golden: GoldenCase,
    ) -> JuryResult:
        """并行调用所有评委，聚合结果。"""
        if trace.error:
            return JuryResult(
                strategy=self.strategy,
                meta_review=f"跳过：Agent 执行失败 ({trace.error})",
            )

        # ── 1. 并行评分（信号量限流）──
        async def _one(j: JudgeConfig) -> JudgeScore:
            async with self._get_semaphore():
                return await llm_judge(
                    trace, golden, j.model, req_id=f"jury_{j.name}",
                )

        raw = await asyncio.gather(
            *[_one(j) for j in self.judges],
            return_exceptions=True,
        )
        scores: list[JudgeScore] = []
        for i, item in enumerate(raw):
            if isinstance(item, BaseException):
                logger.error("[Jury] 评委 %s 异常: %s", self.judges[i].name, item)
                scores.append(JudgeScore(
                    judge_model=self.judges[i].model,
                    error=f"评委异常: {type(item).__name__}: {item}",
                ))
            else:
                scores.append(item)

        # ── 2. 过滤失败的评委 ──
        valid = [s for s in scores if s.error is None]
        if len(valid) < self.min_judges:
            return JuryResult(
                judge_scores=tuple(scores),
                strategy=self.strategy,
                meta_review=f"有效评委不足 ({len(valid)}/{self.min_judges})",
            )

        # ── 3. 聚合各维度 ──
        all_dims = {
            d.name for s in valid for d in s.dimensions
        }
        aggregated_dims: dict[str, float] = {}
        for dim in all_dims:
            vals = [
                s.dimension(dim).score
                for s in valid
                if s.dimension(dim) is not None
            ]
            if vals:
                aggregated_dims[dim] = self._aggregate(vals)

        aggregated_overall = (
            round(sum(aggregated_dims.values()) / len(aggregated_dims), 2)
            if aggregated_dims else 0.0
        )

        # ── 4. 一致性 + 置信区间 ──
        overalls = [s.overall for s in valid]
        agreement = self._agreement(overalls)
        ci_lower, ci_upper = self._confidence_interval(overalls)

        return JuryResult(
            judge_scores=tuple(scores),
            aggregated_dimensions=aggregated_dims,
            aggregated_overall=aggregated_overall,
            agreement=agreement,
            confidence_lower=ci_lower,
            confidence_upper=ci_upper,
            strategy=self.strategy,
        )

    # ── 内部聚合方法 ──
    def _aggregate(self, values: list[float], weights: list[float] | None = None) -> float:
        if self.strategy == VotingStrategy.MEDIAN:
            return round(statistics.median(values), 2)
        if self.strategy == VotingStrategy.MEAN:
            return round(statistics.mean(values), 2)
        if self.strategy == VotingStrategy.UNANIMOUS:
            if max(values) - min(values) > 1.0:
                raise ValueError(
                    f"评委不一致（分差 {max(values) - min(values):.1f}）"
                )
            return round(statistics.mean(values), 2)
        if self.strategy == VotingStrategy.WEIGHTED:
            if not weights or len(weights) != len(values):
                return round(statistics.mean(values), 2)
            total_w = sum(weights)
            if total_w == 0:
                return round(statistics.mean(values), 2)
            return round(sum(v * w for v, w in zip(values, weights, strict=True)) / total_w, 2)
        return round(statistics.mean(values), 2)

    _AGREEMENT_SCALE = 5.0

    @classmethod
    def _agreement(cls, values: list[float]) -> float:
        """一致性 = 1 - (StdDev / 5.0)，限制在 0-1。"""
        if len(values) < 2:
            return 1.0
        return round(max(0.0, 1.0 - statistics.stdev(values) / cls._AGREEMENT_SCALE), 3)

    _T_TABLE = {
        1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57,
        6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
        15: 2.13, 20: 2.09, 30: 2.04,
    }

    @staticmethod
    def _t_value(df: int) -> float:
        if df in CalibratedJudge._T_TABLE:
            return CalibratedJudge._T_TABLE[df]
        for k in sorted(CalibratedJudge._T_TABLE.keys()):
            if k >= df:
                return CalibratedJudge._T_TABLE[k]
        return 1.96

    @staticmethod
    def _confidence_interval(
        values: list[float],
    ) -> tuple[float | None, float | None]:
        """t 分布近似置信区间（95%，双尾）。"""
        n = len(values)
        if n < 2:
            return None, None
        mean = statistics.mean(values)
        stdev = statistics.stdev(values)
        t = CalibratedJudge._t_value(n - 1)
        margin = t * stdev / (n ** 0.5)
        return round(mean - margin, 2), round(mean + margin, 2)
