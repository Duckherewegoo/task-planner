"""
核心评估引擎：CrucibleEvaluator
运行测试用例，聚合指标，生成详细报告
"""

import asyncio
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import defaultdict

from .dataset_manager import TestCase, DatasetManager
from .harness.agent_harness import AgentHarness
from .harness.evaluators import Evaluators
from .hitl_reviewer import HITLReviewer
from .harness.reporters import Reporters
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("eval.crucible")


class CrucibleEvaluator:
    """
    评估引擎：处理测试数据集，运行 Agent，计算指标，生成报告
    支持并行执行（限制并发数）
    """

    def __init__(
        self,
        dataset: List[TestCase],
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
        self.results: List[Dict[str, Any]] = []
        self.summary = {}

    async def run(self) -> Dict[str, Any]:
        """
        执行评估
        """
        logger.info(f"Starting Crucible evaluation on {len(self.dataset)} cases")
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def evaluate_one(case: TestCase) -> Dict[str, Any]:
            async with semaphore:
                harness = AgentHarness(enable_refine=self.enable_refine, timeout=self.timeout)
                result = await harness.run(case.input)
                # 计算指标
                metrics = self._compute_metrics(case, result)
                result["metrics"] = metrics
                result["case_id"] = case.id
                result["case_category"] = case.category
                result["case_complexity"] = case.complexity
                result["expected_needs_planning"] = case.expected_needs_planning
                # 标记是否需要人工审查
                if self.enable_hitl and self._needs_hitl(metrics, result):
                    result["hitl_required"] = True
                return result

        tasks = [evaluate_one(case) for case in self.dataset]
        self.results = await asyncio.gather(*tasks)

        # 聚合汇总
        self.summary = self._aggregate_results(self.results)
        return self.summary

    def _compute_metrics(self, case: TestCase, result: Dict[str, Any]) -> Dict[str, float]:
        """
        针对单个测试用例计算多个指标
        """
        metrics = {}

        # 1. 准确性：期望输出 vs 实际输出
        if case.expected_output and result.get("direct_response"):
            metrics["accuracy"] = Evaluators.accuracy(case.expected_output, result["direct_response"])
            metrics["exact_match"] = 1.0 if Evaluators.exact_match(case.expected_output, result["direct_response"]) else 0.0
        else:
            metrics["accuracy"] = 0.0
            metrics["exact_match"] = 0.0

        # 2. 完整性：是否生成计划（若期望规划）
        if case.expected_needs_planning:
            metrics["plan_generated"] = 1.0 if len(result.get("nodes", [])) > 0 else 0.0
            metrics["task_id_created"] = 1.0 if result.get("task_id") else 0.0
        else:
            metrics["plan_generated"] = 1.0 if result.get("direct_response") else 0.0
            metrics["task_id_created"] = 1.0 if result.get("task_id") else 0.0

        # 3. 节点数量准确性（若期望节点数）
        if case.expected_plan_nodes is not None and len(result.get("nodes", [])) > 0:
            metrics["node_count_accuracy"] = Evaluators.plan_node_count_accuracy(
                case.expected_plan_nodes, result.get("nodes", [])
            )
        else:
            metrics["node_count_accuracy"] = 1.0

        # 4. 无环检测
        metrics["acyclic"] = 1.0 if Evaluators.check_acyclic(
            result.get("nodes", []), result.get("edges", [])
        ) else 0.0

        # 5. 相关性（如果输出存在）
        if result.get("direct_response"):
            metrics["relevance"] = Evaluators.response_relevance(
                result["direct_response"], case.input
            )
        else:
            metrics["relevance"] = 0.0

        # 6. 延迟是否可接受
        elapsed = result.get("elapsed", 999)
        metrics["latency_acceptable"] = 1.0 if elapsed <= 10.0 else 0.0
        metrics["elapsed_seconds"] = elapsed

        # 7. 成功率
        metrics["success"] = 1.0 if result.get("success") else 0.0

        return metrics

    def _needs_hitl(self, metrics: Dict[str, float], result: Dict[str, Any]) -> bool:
        """判断哪些 case 需要人工审查"""
        # 规则：成功率低、准确率低、延迟超时、或没有生成计划
        if metrics["success"] < 0.5:
            return True
        if metrics["accuracy"] < 0.4:
            return True
        if metrics["plan_generated"] < 0.5 and result.get("expected_needs_planning"):
            return True
        if metrics["latency_acceptable"] < 0.5:
            return True
        return False

    def _aggregate_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """聚合所有结果，生成汇总统计"""
        total = len(results)
        if total == 0:
            return {"total": 0}

        # 按类别和复杂度分组
        categories = defaultdict(list)
        complexities = defaultdict(list)

        for r in results:
            cat = r.get("case_category", "unknown")
            comp = r.get("case_complexity", "unknown")
            categories[cat].append(r)
            complexities[comp].append(r)

        def agg_metrics(metric_list):
            """计算一组结果的指标平均值"""
            if not metric_list:
                return {}
            keys = metric_list[0]["metrics"].keys()
            avg = {}
            for k in keys:
                values = [m["metrics"][k] for m in metric_list if k in m["metrics"]]
                if values:
                    avg[k] = sum(values) / len(values)
            return avg

        summary = {
            "total": total,
            "success_rate": sum(1 for r in results if r["metrics"]["success"] > 0.5) / total,
            "avg_accuracy": sum(r["metrics"]["accuracy"] for r in results) / total,
            "avg_relevance": sum(r["metrics"]["relevance"] for r in results) / total,
            "avg_latency": sum(r["metrics"]["elapsed_seconds"] for r in results) / total,
            "acyclic_rate": sum(1 for r in results if r["metrics"]["acyclic"] > 0.5) / total,
            "plan_generation_rate": sum(1 for r in results if r["metrics"]["plan_generated"] > 0.5) / total,
            "by_category": {},
            "by_complexity": {},
            "hitl_required": [r for r in results if r.get("hitl_required", False)],
            "timestamp": datetime.utcnow().isoformat(),
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

    def generate_report(self, format: str = "json") -> str:
        """
        生成评估报告
        """
        if format == "json":
            return json.dumps({
                "summary": self.summary,
                "results": self.results,
            }, indent=2, default=str)
        elif format == "markdown":
            return Reporters.markdown_report(self.summary, self.results)
        elif format == "html":
            return Reporters.html_report(self.summary, self.results)
        else:
            raise ValueError(f"Unsupported format: {format}")


def create_default_dataset() -> List[TestCase]:
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
        # 添加更多...
    ]
