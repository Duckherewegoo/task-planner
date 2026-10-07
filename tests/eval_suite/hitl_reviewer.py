"""
人工审查 (Human-in-the-Loop) 工具
标记需要审查的案例，提供交互式审查界面（CLI）

Changelog:
  ✅ P0-1：datetime.utcnow() → datetime.now(timezone.utc)（两处）
  ✅ P0-2：mkdir 加 parents=True
  ✅ P0-3：load_review_results 加异常处理 + schema 校验，文件不存在/损坏不再崩
  ✅ P0-4：interactive_review 支持中途退出（Ctrl+C / EOF）并保存已审查结果；
           score 输入加 1-5 范围校验
  ✅ P1-1：flag_for_review 的 success 判断改为 == 0（更符合 0/1 语义）
  ✅ P1-2：interactive_review 每审一条立即落盘（防止崩溃丢失）
  ✅ P1-3：review_summary 增加分数分布、中位数
  ✅ P2-1：日志统一 %s 风格
  ✅ P2-2：增加 dry_run 模式，预览要审查哪些 case
  ✅ P2-3：增加 _safe_review_path 防路径穿越
"""

import json
from datetime import (
    UTC,
    datetime,
    timezone,
)
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("eval.hitl")


# ── 合法评分范围 ──
_SCORE_MIN = 1
_SCORE_MAX = 5


class HITLReviewer:
    """
    人工审查管理器
    """

    def __init__(self, review_dir: str = "reviews"):
        self.review_dir = Path(review_dir)
        # ✅ P0-2 修复：parents=True 支持多层目录
        self.review_dir.mkdir(parents=True, exist_ok=True)

    # ────────────────────────────────────────────
    #  标记
    # ────────────────────────────────────────────

    def flag_for_review(
        self,
        results: list[dict[str, Any]],
        threshold: float = 0.5,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """
        筛选需要审查的结果。

        ✅ P1-1 修复：success 是 0/1，用 == 0 比 < 0.5 语义更明确。
        ✅ P2-2 新增：dry_run=True 时只打印不返回，方便预览。

        触发条件（任一满足）：
          - accuracy < threshold（默认 0.5）
          - success == 0（完全失败）
          - 上游显式标记 hitl_required
        """
        flagged: list[dict[str, Any]] = []

        for r in results:
            metrics = r.get("metrics", {}) or {}

            accuracy = metrics.get("accuracy", 1.0)
            success = metrics.get("success", 1.0)

            reasons = []
            if accuracy < threshold:
                reasons.append(f"accuracy={accuracy:.2f}<{threshold}")
            if success == 0:
                reasons.append("success=0")
            if r.get("hitl_required", False):
                reasons.append("upstream_flagged")

            if reasons:
                r_copy = dict(r)
                r_copy["_flag_reasons"] = reasons
                flagged.append(r_copy)

        logger.info(
            "[HITL] Flagged %d / %d cases for review",
            len(flagged), len(results),
        )

        if dry_run:
            print(f"\n=== Dry-run: {len(flagged)} cases would be flagged ===")
            for i, r in enumerate(flagged, 1):
                print(
                    f"  {i}. id={r.get('case_id')} "
                    f"category={r.get('case_category')} "
                    f"reasons={r.get('_flag_reasons')}"
                )
            print()

        return flagged

    # ────────────────────────────────────────────
    #  持久化
    # ────────────────────────────────────────────

    def _safe_review_path(self, filename: str) -> Path:
        """
        ✅ P2-3 新增：防止用户通过 ../../ 路径穿越写入非 review_dir 位置。
        """
        # 只取 basename，丢弃任何目录成分
        safe_name = Path(filename).name
        full_path = (self.review_dir / safe_name).resolve()
        base = self.review_dir.resolve()
        if not str(full_path).startswith(str(base)):
            raise ValueError(
                f"[HITL] 非法路径: {filename} 解析后不在 {base} 内"
            )
        return full_path

    def save_review_requests(self, flagged: list[dict[str, Any]]) -> str:
        """
        保存审查请求到文件。

        ✅ P0-1 修复：datetime.now(timezone.utc)
        ✅ P2-3 修复：路径安全校验
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"review_requests_{timestamp}.json"
        file_path = self._safe_review_path(filename)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(flagged, f, indent=2, ensure_ascii=False, default=str)

        logger.info("[HITL] Review requests saved to %s", file_path)
        return str(file_path)

    def save_review_results(
        self,
        reviewed: list[dict[str, Any]],
        source_path: str | None = None,
    ) -> str:
        """
        ✅ P2-2 新增：把审查结果写回文件（原来 interactive_review 结果只在内存里）。
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"review_results_{timestamp}.json"
        file_path = self._safe_review_path(filename)

        payload = {
            "source": source_path,
            "reviewed_count": len(reviewed),
            "reviewed_at": datetime.now(UTC).isoformat(),
            "items": reviewed,
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

        logger.info("[HITL] Review results saved to %s (%d items)",
                    file_path, len(reviewed))
        return str(file_path)

    def load_review_results(self, file_path: str) -> list[dict[str, Any]]:
        """
        加载已完成的审查结果。

        ✅ P0-3 修复：
          - 文件不存在 → 返回空列表（不再 FileNotFoundError）
          - JSON 损坏 → 返回空列表并记录
          - 支持两种 schema：裸 list 或 {"items": [...]}
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning("[HITL] Review file not found: %s", file_path)
            return []

        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            logger.error("[HITL] Review file JSON 损坏: %s | err=%s", file_path, e)
            return []
        except OSError as e:
            logger.error("[HITL] Review file 读取失败: %s | err=%s", file_path, e)
            return []

        # 兼容两种结构
        if isinstance(raw, dict):
            items = raw.get("items", [])
        elif isinstance(raw, list):
            items = raw
        else:
            logger.error(
                "[HITL] Review file 结构无法识别: type=%s", type(raw).__name__,
            )
            return []

        if not isinstance(items, list):
            logger.error(
                "[HITL] Review items 不是 list: type=%s", type(items).__name__,
            )
            return []

        logger.info("[HITL] Loaded %d reviewed items from %s", len(items), file_path)
        return items

    # ────────────────────────────────────────────
    #  交互式审查
    # ────────────────────────────────────────────

    @staticmethod
    def _parse_score(raw: str) -> int | None:
        """
        ✅ P0-4 修复：解析用户输入评分。
          - 空串 / 非数字 → None
          - 超出 [1, 5] → None
          - 合法 → int
        """
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            v = int(raw)
        except ValueError:
            return None
        if _SCORE_MIN <= v <= _SCORE_MAX:
            return v
        return None

    def interactive_review(
        self,
        flagged: list[dict[str, Any]],
        autosave_every: int = 1,
    ) -> list[dict[str, Any]]:
        """
        CLI 交互式审查。

        ✅ P0-4 修复：
          - 支持中途 Ctrl+C / EOF，已审查的部分会先落盘再退出
          - score 输入加范围校验，非法值重问而不是直接 None
          - 每审 autosave_every 条立即落盘，防止进程崩溃丢失

        Args:
            flagged: 待审查列表
            autosave_every: 每审 N 条保存一次，默认 1（最安全）

        Returns:
            已审查的 items 列表（可能少于输入，若中途退出）
        """
        reviewed: list[dict[str, Any]] = []
        interrupted = False

        print(f"\n{'=' * 60}")
        print(f"  Interactive Review: {len(flagged)} cases")
        print("  Ctrl+C 或输入 q 退出（已审查部分会自动保存）")
        print(f"{'=' * 60}\n")

        try:
            for idx, item in enumerate(flagged, 1):
                print(f"\n----- Review {idx}/{len(flagged)} -----")
                print(f"  Case ID:  {item.get('case_id', 'N/A')}")
                print(f"  Category: {item.get('case_category', 'N/A')}")
                print(f"  Input:    {item.get('input', 'N/A')}")
                print(f"  Output:   {str(item.get('direct_response', 'N/A'))[:200]}")
                print(f"  Nodes:    {len(item.get('nodes', []))}")
                try:
                    elapsed = float(item.get("elapsed", 0))
                    print(f"  Elapsed:  {elapsed:.2f}s")
                except (TypeError, ValueError):
                    print("  Elapsed:  N/A")
                print(f"  Metrics:  {item.get('metrics', {})}")
                if item.get("_flag_reasons"):
                    print(f"  Flagged:  {item['_flag_reasons']}")

                # ── 评分（带重试）──
                score: int | None = None
                while True:
                    raw = input(
                        f"\n  Score ({_SCORE_MIN}-{_SCORE_MAX}, 回车跳过, q=退出): "
                    ).strip()
                    if raw.lower() == "q":
                        interrupted = True
                        break
                    if not raw:
                        break
                    score = self._parse_score(raw)
                    if score is not None:
                        break
                    print(f"  ⚠️  非法输入，请输入 {_SCORE_MIN}-{_SCORE_MAX} 或回车跳过")

                if interrupted:
                    break

                # ── 评论 ──
                comment = input("  Comment (回车跳过): ").strip()

                reviewed.append({
                    **item,
                    "human_score": score,
                    "human_comment": comment,
                    "reviewed_at": datetime.now(UTC).isoformat(),
                })

                # ── 自动落盘 ──
                if autosave_every > 0 and len(reviewed) % autosave_every == 0:
                    try:
                        self.save_review_results(reviewed)
                    except Exception as e:
                        logger.warning("[HITL] autosave 失败: %s", e)

        except (KeyboardInterrupt, EOFError):
            interrupted = True
            print("\n\n⚠️  审查被中断，正在保存已完成的部分...")

        # ── 最终落盘（无论正常结束还是中断）──
        if reviewed:
            final_path = self.save_review_results(reviewed)
            print(f"\n✅ 已审查 {len(reviewed)} 条，结果保存至: {final_path}")
            if interrupted and len(reviewed) < len(flagged):
                print(
                    f"⚠️  中断退出：剩余 {len(flagged) - len(reviewed)} 条未审查"
                )

        return reviewed

    # ────────────────────────────────────────────
    #  汇总
    # ────────────────────────────────────────────

    def review_summary(self, reviewed: list[dict[str, Any]]) -> dict[str, Any]:
        """
        生成审查摘要。

        ✅ P1-3 修复：增加中位数、分数分布、无评分占比。
        """
        scores = [
            r.get("human_score")
            for r in reviewed
            if r.get("human_score") is not None
        ]

        # 分数分布 {1: n, 2: n, ...}
        score_dist: dict[int, int] = dict.fromkeys(range(_SCORE_MIN, _SCORE_MAX + 1), 0)
        for s in scores:
            if _SCORE_MIN <= s <= _SCORE_MAX:
                score_dist[s] = score_dist.get(s, 0) + 1

        # 中位数
        median = None
        if scores:
            sorted_scores = sorted(scores)
            n = len(sorted_scores)
            median = (
                sorted_scores[n // 2]
                if n % 2 == 1
                else (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2
            )

        return {
            "total_reviewed": len(reviewed),
            "scored_count": len(scores),
            "unscored_count": len(reviewed) - len(scores),
            "avg_human_score": sum(scores) / len(scores) if scores else 0.0,
            "median_human_score": median,
            "score_distribution": score_dist,
            "scores": scores,
            "comments": [
                r.get("human_comment")
                for r in reviewed
                if r.get("human_comment")
            ],
        }
