"""
核心评估引擎：CrucibleEvaluator
运行测试用例，聚合指标，生成详细报告

Changelog:
  ✅ P0-1：asyncio.gather 加 return_exceptions=True，
           单个 case 异常不再拖垮整个评估批次；
           异常统一转成"失败结果"进入聚合。
  ✅ P1-1：datetime.utcnow() → datetime.now(timezone.utc)
  ✅ P1-2：plan_generated 语义拆分为 plan_generated / direct_answer_provided，
           _needs_hitl 判断不再混用。
  ✅ P1-3：agg_metrics 用并集收集 keys，避免漏指标。
  ✅ P2-1：evaluate_one 加 try/except + asyncio.wait_for，
           每个 case 有独立的双层超时保护。
  ✅ P2-2：elapsed 字段不再混入 0/1 指标区；单独放 metrics["elapsed_seconds"]。
  ✅ P2-3：logger 改 %s 风格。
  ✅ P2-4：metrics["success"] 语义与 harness.success 对齐。
"""

import asyncio
import json
from collections import defaultdict
from datetime import (
    UTC,
    datetime,
    timezone,
)
from typing import (
    Any,
)

from task_planner.infrastructure.logger_setup import get_logger

from .dataset_manager import (
    DatasetManager,
    TestCase,
)
from .harness.agent_harness import AgentHarness
from .harness.evaluators import Evaluators
from .harness.reporters import Reporters
from .hitl_reviewer import HITLReviewer

logger = get_logger("eval.crucible")


# ── 失败 case 的兜底 metrics（保持 schema 完整） ──
_EMPTY_METRICS: dict[str, float] = {
    "accuracy": 0.0,
    "exact_match": 0.0,
    "plan_generated": 0.0,
    "direct_answer_provided": 0.0,
    "task_id_created": 0.0,
    "node_count_accuracy": 0.0,
    "acyclic": 1.0,           # 无节点视为"无环"，不应该罚分
    "relevance": 0.0,
    "latency_acceptable": 1.0,  # 未跑起来不算超时
    "elapsed_seconds": 0.0,
    "success": 0.0,
}


class CrucibleEvaluator:
    """
    评估引擎：处理测试数据集，运行 Agent，计算指标，生成报告
    支持并行执行（限制并发数）
    """

    def __init__(
        self,
        dataset: list[TestCase],
        enable_refine: bool = True,
        max_concurrent: int = 3,
        timeout: int = 300,
        enable_hitl: bool = False,
    ):
        self.dataset = dataset
        self.enable_refine = enable_refine
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.enable_hitl = enable_hitl
        self.results: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}

    # ══════════════════════════════════════════════════
    #  主流程
    # ══════════════════════════════════════════════════

    async def run(self) -> dict[str, Any]:
        """执行评估"""
        logger.info("Starting Crucible evaluation on %d cases", len(self.dataset))
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def evaluate_one(case: TestCase) -> dict[str, Any]:
            async with semaphore:
                # ✅ P2-1 修复：单 case 独立 try/except，双保险
                #    外层 asyncio.wait_for 是 harness 内部超时失效时的兜底
                try:
                    harness = AgentHarness(
                        enable_refine=self.enable_refine,
                        timeout=self.timeout,
                    )
                    # harness 内部已有 timeout，这里加 +10s 余量兜底
                    result = await asyncio.wait_for(
                        harness.run(case.input),
                        timeout=self.timeout + 10,
                    )
                except TimeoutError:
                    logger.error(
                        "[Eval] case %s 外层超时 (%ds)",
                        case.id, self.timeout + 10,
                    )
                    result = {
                        "input": case.input,
                        "error": f"Outer timeout after {self.timeout + 10}s",
                        "success": False,
                        "direct_response": "",
                        "nodes": [],
                        "edges": [],
                        "task_id": "",
                        "elapsed": float(self.timeout + 10),
                    }
                except Exception as e:
                    logger.exception("[Eval] case %s 异常: %s", case.id, e)
                    result = {
                        "input": case.input,
                        "error": f"{type(e).__name__}: {e}",
                        "success": False,
                        "direct_response": "",
                        "nodes": [],
                        "edges": [],
                        "task_id": "",
                        "elapsed": 0.0,
                    }

                # 计算指标
                metrics = self._compute_metrics(case, result)
                result["metrics"] = metrics
                result["case_id"] = case.id
                result["case_category"] = case.category
                result["case_complexity"] = case.complexity
                result["expected_needs_planning"] = case.expected_needs_planning

                if self.enable_hitl and self._needs_hitl(metrics, result):
                    result["hitl_required"] = True
                return result

        tasks = [evaluate_one(case) for case in self.dataset]

        # ✅ P0-1 修复：return_exceptions=True
        #    之前一个 case 抛异常会取消所有其他正在跑的 case。
        #    现在异常会被收集，然后统一转成"失败结果"。
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        normalized: list[dict[str, Any]] = []
        for case, r in zip(self.dataset, raw_results):
            if isinstance(r, BaseException):
                logger.error(
                    "[Eval] case %s 未预期异常: %s: %s",
                    case.id, type(r).__name__, r,
                )
                normalized.append({
                    "input": case.input,
                    "error": f"{type(r).__name__}: {r}",
                    "success": False,
                    "direct_response": "",
                    "nodes": [],
                    "edges": [],
                    "task_id": "",
                    "elapsed": 0.0,
                    "metrics": dict(_EMPTY_METRICS),
                    "case_id": case.id,
                    "case_category": case.category,
                    "case_complexity": case.complexity,
                    "expected_needs_planning": case.expected_needs_planning,
                })
            else:
                normalized.append(r)

        self.results = normalized
        self.summary = self._aggregate_results(self.results)
        logger.info(
            "[Eval] 完成: total=%d, success=%d/%d",
            len(self.results),
            sum(1 for r in self.results if r["metrics"].get("success", 0) > 0.5),
            len(self.results),
        )
        return self.summary

    # ══════════════════════════════════════════════════
    #  指标计算
    # ══════════════════════════════════════════════════

    def _compute_metrics(
        self,
        case: TestCase,
        result: dict[str, Any],
    ) -> dict[str, float]:
        """
        针对单个测试用例计算多个指标。

        ✅ P1-2 修复：plan_generated 与 direct_answer_provided 语义分离。
          原实现中"规划任务"和"直接回答任务"共用 plan_generated 字段，
          导致聚合时把两种完全不同的东西平均在一起。
        """
        metrics: dict[str, float] = {}

        # 1. 准确性
        if case.expected_output and result.get("direct_response"):
            metrics["accuracy"] = Evaluators.accuracy(
                case.expected_output, result["direct_response"]
            )
            metrics["exact_match"] = (
                1.0 if Evaluators.exact_match(
                    case.expected_output, result["direct_response"]
                ) else 0.0
            )
        else:
            metrics["accuracy"] = 0.0
            metrics["exact_match"] = 0.0

        # 2. 完整性（✅ P1-2：拆分两个语义）
        if case.expected_needs_planning:
            metrics["plan_generated"] = 1.0 if result.get("nodes") else 0.0
            metrics["direct_answer_provided"] = 0.0
        else:
            metrics["plan_generated"] = 0.0
            metrics["direct_answer_provided"] = (
                1.0 if result.get("direct_response") else 0.0
            )
        metrics["task_id_created"] = 1.0 if result.get("task_id") else 0.0

        # 3. 节点数量准确性
        if case.expected_plan_nodes is not None and result.get("nodes"):
            metrics["node_count_accuracy"] = Evaluators.plan_node_count_accuracy(
                case.expected_plan_nodes, result.get("nodes", [])
            )
        else:
            metrics["node_count_accuracy"] = 1.0

        # 4. 无环检测
        metrics["acyclic"] = 1.0 if Evaluators.check_acyclic(
            result.get("nodes", []), result.get("edges", [])
        ) else 0.0

        # 5. 相关性
        if result.get("direct_response"):
            metrics["relevance"] = Evaluators.response_relevance(
                result["direct_response"], case.input
            )
        else:
            metrics["relevance"] = 0.0

        # 6. 延迟（✅ P2-2：elapsed 单独放，不与 0/1 指标混在一起）
        elapsed = result.get("elapsed")
        if elapsed is None:
            elapsed = float("inf")
        metrics["latency_acceptable"] = (
            1.0 if Evaluators.latency_acceptance(elapsed) else 0.0
        )
        metrics["elapsed_seconds"] = (
            elapsed if elapsed != float("inf") else 0.0
        )

        # 7. 成功率
        metrics["success"] = 1.0 if result.get("success") else 0.0

        return metrics

    def _needs_hitl(
        self,
        metrics: dict[str, float],
        result: dict[str, Any],
    ) -> bool:
        """
        判断哪些 case 需要人工审查。

        ✅ P1-2 修复：使用拆分后的字段，不再依赖语义混用的 plan_generated。
        """
        if metrics["success"] < 0.5:
            return True
        if metrics["accuracy"] < 0.4:
            return True

        # 期望规划但没生成节点 → 需要审查
        if result.get("expected_needs_planning") and metrics.get("plan_generated", 0) < 0.5:
            return True
        # 期望直接回答但没产出 → 需要审查
        if not result.get("expected_needs_planning") and metrics.get("direct_answer_provided", 0) < 0.5:
            return True
        # 延迟超阈值 → 需要审查
        if metrics["latency_acceptable"] < 0.5:
            return True
        return False

    # ══════════════════════════════════════════════════
    #  聚合
    # ══════════════════════════════════════════════════

    def _aggregate_results(
        self,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """聚合所有结果，生成汇总统计"""
        total = len(results)
        if total == 0:
            return {"total": 0}

        categories: dict[str, list] = defaultdict(list)
        complexities: dict[str, list] = defaultdict(list)

        for r in results:
            categories[r.get("case_category", "unknown")].append(r)
            complexities[r.get("case_complexity", "unknown")].append(r)

        # ✅ P1-3 修复：用并集收集 keys，避免某个 case 缺字段导致漏指标
        def agg_metrics(metric_list: list[dict[str, Any]]) -> dict[str, float]:
            if not metric_list:
                return {}
            all_keys: set[str] = set()
            for m in metric_list:
                all_keys.update((m.get("metrics") or {}).keys())

            avg: dict[str, float] = {}
            for k in all_keys:
                values = [
                    m["metrics"][k]
                    for m in metric_list
                    if k in (m.get("metrics") or {})
                ]
                if values:
                    avg[k] = sum(values) / len(values)
            return avg

        def safe_avg(field: str) -> float:
            vals = [
                r["metrics"].get(field, 0.0)
                for r in results
                if isinstance(r.get("metrics"), dict)
            ]
            return sum(vals) / len(vals) if vals else 0.0

        # ✅ P1-1 修复：带时区的 UTC
        summary: dict[str, Any] = {
            "total": total,
            "success_rate": sum(
                1 for r in results
                if r.get("metrics", {}).get("success", 0) > 0.5
            ) / total,
            "avg_accuracy": safe_avg("accuracy"),
            "avg_relevance": safe_avg("relevance"),
            "avg_latency": safe_avg("elapsed_seconds"),
            "acyclic_rate": sum(
                1 for r in results
                if r.get("metrics", {}).get("acyclic", 0) > 0.5
            ) / total,
            # ✅ P1-2 修复：按期望规划/直接回答分别统计
            "plan_generation_rate": self._rate_planning_success(results),
            "by_category": {},
            "by_complexity": {},
            "hitl_required": [
                r for r in results if r.get("hitl_required", False)
            ],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        for cat, cat_results in categories.items():
            summary["by_category"][cat] = {
                "count": len(cat_results),
                "metrics": agg_metrics(cat_results),
            }

        for comp, comp_results in complexities.items():
            summary["by_complexity"][comp] = {
                "count": len(comp_results),
                "metrics": agg_metrics(comp_results),
            }

        return summary

    @staticmethod
    def _rate_planning_success(results: list[dict[str, Any]]) -> float:
        """
        期望规划的任务里，实际生成节点的比例。

        ✅ P1-2 补充：让"规划生成率"语义明确。
        """
        planning_cases = [
            r for r in results if r.get("expected_needs_planning")
        ]
        if not planning_cases:
            return 0.0
        return sum(
            1 for r in planning_cases
            if r.get("metrics", {}).get("plan_generated", 0) > 0.5
        ) / len(planning_cases)

    # ══════════════════════════════════════════════════
    #  报告
    # ══════════════════════════════════════════════════

    def generate_report(self, format: str = "json") -> str:
        """生成评估报告"""
        if format == "json":
            return json.dumps(
                {"summary": self.summary, "results": self.results},
                indent=2,
                default=str,
            )
        elif format == "markdown":
            return Reporters.markdown_report(self.summary, self.results)
        elif format == "html":
            return Reporters.html_report(self.summary, self.results)
        else:
            raise ValueError(f"Unsupported format: {format}")


# ══════════════════════════════════════════════════
#  默认测试数据集
# ══════════════════════════════════════════════════

def create_default_dataset() -> list[TestCase]:
    """创建默认测试数据集（示例）"""
    return [
        TestCase(
            id="cooking_1",
            input="教我做甜口西红柿炒鸡蛋，要详细步骤",
            expected_output="西红柿切块，鸡蛋打散，热油炒蛋盛出，炒西红柿加糖，混合出锅",
            category="cooking",
            complexity="medium",
            expected_plan_nodes=4,
            expected_needs_planning=True,
        ),
        TestCase(
            id="consult_1",
            input="今天天气怎么样？不需要规划",
            expected_output="抱歉，我无法获取实时天气信息",
            category="consultation",
            complexity="simple",
            expected_needs_planning=False,
        ),
        TestCase(
            id="engineering_1",
            input="帮我搭建一个 K8s 生产集群的详细步骤",
            expected_plan_nodes=6,
            category="engineering",
            complexity="complex",
            expected_needs_planning=True,
        ),
    ]
