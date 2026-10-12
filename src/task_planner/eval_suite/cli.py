"""cli.py — eval_suite 命令行入口。"""
import argparse, asyncio, json, sys
from pathlib import Path

from .types import CaseResult, Difficulty, GoldenCase
from .golden.generator import generate_dataset
from .runners.agent_runner import run_agent
from .scorers.deterministic import score_deterministic
from .jury.calibrated_judge import CalibratedJudge, JudgeConfig
from .report.json_report import write_json_report
from .report.html_report import render_html_report
from .hil.sheet import generate_review_sheet


def _load_dataset(d: Path) -> list[GoldenCase]:
    out: list[GoldenCase] = []
    for p in sorted(d.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        try:
            diff = Difficulty(data.get("difficulty", "medium"))
        except ValueError:
            diff = Difficulty.MEDIUM
        out.append(GoldenCase(
            case_id=data["case_id"], user_input=data["user_input"],
            expected_plan=data["expected_plan"],
            expected_intent=data["expected_intent"],
            tags=tuple(data.get("tags", [])),
            difficulty=diff, validated=True,
        ))
    return out


async def _cmd_generate(args):
    specs = json.loads(Path(args.specs).read_text(encoding="utf-8"))
    cases = await generate_dataset(specs, Path(args.output))
    print(f"✅ 生成 {len(cases)} 条")
    return 0


async def _cmd_run(args):
    cases = _load_dataset(Path(args.dataset))
    if not cases:
        print(f"⚠️  {args.dataset} 里没有 json，先把黄金标准放进去")
        return 1

    use_mock = True if args.mock else (False if args.live else None)

    judge = None
    if args.jury:
        models = args.judge_models or ["qwen3.7-plus", "qwen3.7-flash"]
        judge = CalibratedJudge([
            JudgeConfig(model=m, name=f"j{i}") for i, m in enumerate(models)
        ])

    results: list[CaseResult] = []
    for case in cases:
        print(f"  ▶ {case.case_id}")
        trace = await run_agent(case.case_id, case.user_input, use_mock=use_mock)
        det = score_deterministic(trace, case)
        jury = await judge.evaluate(trace, case) if (judge and not trace.error) else None
        results.append(CaseResult(
            case_id=case.case_id, user_input=case.user_input,
            difficulty=case.difficulty, duration_sec=trace.duration_sec,
            error=trace.error, mock_mode=trace.mock_mode,
            deterministic=det, jury=jury,
        ))
        print(f"    确定性={det.overall:.2f}")

    out = Path(args.output)
    write_json_report(results, out / "eval_report.json")
    render_html_report(results, out / "eval_report.html")
    generate_review_sheet(results, out / "hil_review.json")
    print(f"\n✅ 报告 → {out}/")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="eval_suite")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="LLM 合成黄金标准")
    g.add_argument("--specs", required=True)
    g.add_argument("--output", default="eval_data/")

    r = sub.add_parser("run", help="运行评估")
    r.add_argument("--dataset", default="eval_data/")
    r.add_argument("--mock", action="store_true")
    r.add_argument("--live", action="store_true")
    r.add_argument("--jury", action="store_true")
    r.add_argument("--judge-models", nargs="*", default=[])
    r.add_argument("--output", default="eval_reports/")

    args = ap.parse_args()
    if args.cmd == "generate":
        return asyncio.run(_cmd_generate(args))
    if args.cmd == "run":
        if args.mock and args.live:
            print("❌ --mock 与 --live 不能同时用")
            return 2
        return asyncio.run(_cmd_run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
