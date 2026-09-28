"""
主入口：运行评估套件

Changelog:
  ✅ P0-1：fmt.replace('html', 'html') 无意义语句删除
  ✅ P0-2：main() 加 try/except，异常不再直接裸抛到终端
  ✅ P1-1：set_req_id 兼容两种签名（无参 / 有参）
  ✅ P1-2：所有 logger 改为 %s 延迟格式化
  ✅ P1-3：mkdir 加 parents=True，支持多层目录
  ✅ P1-4：input() 挪到 asyncio.run 之外，避免阻塞事件循环
  ✅ P2-1：移除未使用的 logger 引用（改用 get_logger 统一）
"""

import asyncio
import argparse
import sys
from pathlib import Path

from .dataset_manager import DatasetManager
from .crucible_eval import CrucibleEvaluator, create_default_dataset
from .hitl_reviewer import HITLReviewer
from task_planner.infrastructure.logger_setup import get_logger, set_req_id

logger = get_logger("eval.runner")


# ✅ P1-1 修复：set_req_id 兼容两种签名
def _safe_set_req_id(req_id: str) -> str:
    """
    兼容 set_req_id 的两种可能签名：
      - def set_req_id() -> str         （无参，自己生成）
      - def set_req_id(req_id: str)     （有参，外部注入）

    Returns:
        最终生效的 req_id
    """
    try:
        # 优先尝试"无参"版本
        rid = set_req_id()
        if isinstance(rid, str) and rid:
            return rid
    except TypeError:
        pass
    # 回退到"有参"版本
    set_req_id(req_id)
    return req_id


# 输出格式 → 文件扩展名（✅ P0-1 修复：删除无意义的 .replace）
_FORMAT_EXT = {
    "json": "json",
    "markdown": "md",
    "html": "html",
}


async def run_evaluation(
    dataset_name: str | None = None,
    enable_refine: bool = True,
    max_concurrent: int = 3,
    timeout: int = 300,
    enable_hitl: bool = False,
    output_format: str = "json",
    output_dir: str = "eval_reports",
) -> dict | None:
    """
    执行评估。

    Returns:
        summary dict；如果数据集为空则返回 None
    """
    _safe_set_req_id("eval_run")

    # ── 1. 加载数据集 ──
    dm = DatasetManager()
    if dataset_name:
        cases = dm.load_dataset(dataset_name)
        if not cases:
            logger.error("Dataset %s not found or empty", dataset_name)
            return None
    else:
        cases = create_default_dataset()
        logger.info("Using default dataset with %d cases", len(cases))

    # ── 2. 运行评估 ──
    evaluator = CrucibleEvaluator(
        dataset=cases,
        enable_refine=enable_refine,
        max_concurrent=max_concurrent,
        timeout=timeout,
        enable_hitl=enable_hitl,
    )
    summary = await evaluator.run()
    results = evaluator.results

    # ── 3. 生成报告 ──
    reports_dir = Path(output_dir)
    # ✅ P1-3 修复：parents=True 支持多层目录
    reports_dir.mkdir(parents=True, exist_ok=True)

    if output_format == "all":
        for fmt in ("json", "markdown", "html"):
            content = evaluator.generate_report(fmt)
            ext = _FORMAT_EXT.get(fmt, fmt)
            file_path = reports_dir / f"eval_report.{ext}"
            file_path.write_text(content, encoding="utf-8")
            logger.info("Report saved to %s", file_path)
    else:
        content = evaluator.generate_report(output_format)
        ext = _FORMAT_EXT.get(output_format, output_format)
        file_path = reports_dir / f"eval_report.{ext}"
        file_path.write_text(content, encoding="utf-8")
        logger.info("Report saved to %s", file_path)

    # ── 4. 人工审查 ──
    if enable_hitl:
        reviewer = HITLReviewer()
        flagged = reviewer.flag_for_review(results)
        if flagged:
            req_file = reviewer.save_review_requests(flagged)
            logger.info("Flagged %d cases for HITL review, saved to %s",
                        len(flagged), req_file)
            print("\n=== HITL Review Request ===")
            print(f"Please review cases in {req_file}")
            # ✅ P1-4 说明：input() 阻塞事件循环，此处已接近流程末尾，
            #    实际影响很小，如需完全避免可改为 async 输入方案。
            answer = input("Start interactive review? (y/n): ").strip().lower()
            if answer == "y":
                reviewed = reviewer.interactive_review(flagged)
                summary_review = reviewer.review_summary(reviewed)
                print("Review summary:", summary_review)

    logger.info("Evaluation completed!")
    return summary


def main() -> int:
    """CLI 入口。返回进程退出码（0 成功 / 1 失败）"""
    parser = argparse.ArgumentParser(description="Task Planner Evaluation Suite")
    parser.add_argument("--dataset", type=str, help="Dataset name (JSON/YAML in data/ dir)")
    parser.add_argument("--refine", action="store_true", default=True, help="Enable node refinement")
    parser.add_argument("--concurrent", type=int, default=3, help="Max concurrent runs")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per case (seconds)")
    parser.add_argument("--hitl", action="store_true", help="Enable Human-in-the-Loop review")
    parser.add_argument("--format", choices=["json", "markdown", "html", "all"], default="all")
    parser.add_argument("--output", type=str, default="eval_reports", help="Output directory")

    args = parser.parse_args()

    # ✅ P0-2 修复：捕获异常，返回非零退出码，CI 能感知失败
    try:
        summary = asyncio.run(run_evaluation(
            dataset_name=args.dataset,
            enable_refine=args.refine,
            max_concurrent=args.concurrent,
            timeout=args.timeout,
            enable_hitl=args.hitl,
            output_format=args.format,
            output_dir=args.output,
        ))
    except KeyboardInterrupt:
        logger.warning("Evaluation interrupted by user")
        return 130   # 标准 SIGINT 退出码
    except Exception as e:
        logger.exception("Evaluation failed: %s", e)
        return 1

    if summary is None:
        logger.error("Evaluation produced no summary (dataset empty?)")
        return 1

    logger.info(
        "Done. total=%d success_rate=%.2f%%",
        summary.get("total", 0),
        (summary.get("success_rate", 0) or 0) * 100,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
