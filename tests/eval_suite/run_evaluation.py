"""
主入口：运行评估套件
"""

import asyncio
import argparse
from pathlib import Path

from .dataset_manager import DatasetManager
from .crucible_eval import CrucibleEvaluator, create_default_dataset
from .hitl_reviewer import HITLReviewer
from .harness.reporters import Reporters
from task_planner.infrastructure.logger_setup import setup_logger, set_req_id

logger = setup_logger("eval.runner")


async def run_evaluation(
    dataset_name: str = None,
    enable_refine: bool = True,
    max_concurrent: int = 3,
    timeout: int = 300,
    enable_hitl: bool = False,
    output_format: str = "json",
    output_dir: str = "eval_reports",
):
    """
    执行评估
    """
    set_req_id("eval_run")

    # 加载数据集
    dm = DatasetManager()
    if dataset_name:
        cases = dm.load_dataset(dataset_name)
        if not cases:
            logger.error(f"Dataset {dataset_name} not found or empty")
            return
    else:
        # 使用默认数据集
        cases = create_default_dataset()
        logger.info(f"Using default dataset with {len(cases)} cases")

    # 运行评估
    evaluator = CrucibleEvaluator(
        dataset=cases,
        enable_refine=enable_refine,
        max_concurrent=max_concurrent,
        timeout=timeout,
        enable_hitl=enable_hitl,
    )
    summary = await evaluator.run()
    results = evaluator.results

    # 生成报告
    reports_dir = Path(output_dir)
    reports_dir.mkdir(exist_ok=True)

    if output_format == "all":
        for fmt in ["json", "markdown", "html"]:
            content = evaluator.generate_report(fmt)
            file_path = reports_dir / f"eval_report.{fmt.replace('markdown', 'md').replace('html', 'html')}"
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"Report saved to {file_path}")
    else:
        content = evaluator.generate_report(output_format)
        ext = output_format.replace("markdown", "md").replace("html", "html")
        file_path = reports_dir / f"eval_report.{ext}"
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Report saved to {file_path}")

    # 人工审查
    if enable_hitl:
        reviewer = HITLReviewer()
        flagged = reviewer.flag_for_review(results)
        if flagged:
            req_file = reviewer.save_review_requests(flagged)
            logger.info(f"Flagged {len(flagged)} cases for HITL review, saved to {req_file}")
            print("\n=== HITL Review Request ===")
            print(f"Please review cases in {req_file}")
            # 可选：启动交互式审查
            if input("Start interactive review? (y/n): ").strip().lower() == "y":
                reviewed = reviewer.interactive_review(flagged)
                summary_review = reviewer.review_summary(reviewed)
                print("Review summary:", summary_review)

    logger.info("Evaluation completed!")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Task Planner Evaluation Suite")
    parser.add_argument("--dataset", type=str, help="Dataset name (JSON/YAML in data/ dir)")
    parser.add_argument("--refine", action="store_true", default=True, help="Enable node refinement")
    parser.add_argument("--concurrent", type=int, default=3, help="Max concurrent runs")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per case (seconds)")
    parser.add_argument("--hitl", action="store_true", help="Enable Human-in-the-Loop review")
    parser.add_argument("--format", choices=["json", "markdown", "html", "all"], default="all")
    parser.add_argument("--output", type=str, default="eval_reports", help="Output directory")

    args = parser.parse_args()
    asyncio.run(run_evaluation(
        dataset_name=args.dataset,
        enable_refine=args.refine,
        max_concurrent=args.concurrent,
        timeout=args.timeout,
        enable_hitl=args.hitl,
        output_format=args.format,
        output_dir=args.output,
    ))


if __name__ == "__main__":
    main()
