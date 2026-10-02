"""
测试数据集管理器：加载、校验、切分数据集
支持 JSON/YAML 格式，包含 input, expected_output, metadata 等字段

Changelog:
  ✅ P0-1：加载失败的 case 记录 idx + id，不再静默丢弃
  ✅ P0-2：显式校验 raw 是 list，非 list / 空文件不再崩溃
  ✅ P0-3：TestCase 加 __post_init__ 校验，语义冲突字段自动纠正
  ✅ P1-1：load_dataset 加 force_reload 参数
  ✅ P1-2：id 重复检测并警告
  ✅ P1-3：mkdir(parents=True)
  ✅ P1-4：format 参数改名为 file_format（避免遮蔽内置）
  ✅ P2-x：日志统一 %s 风格
"""

import json
from pathlib import Path
from typing import (
    List,
    Dict,
    Any,
    Optional,
)
from dataclasses import (
    dataclass,
    field,
)

import yaml

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("eval.dataset_manager")


@dataclass
class TestCase:
    """单个测试用例"""
    id: str
    input: str
    expected_output: Optional[str] = None
    category: str = "general"
    complexity: str = "medium"      # simple | medium | complex
    expected_plan_nodes: Optional[int] = None
    expected_needs_planning: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """✅ P0-3 修复：字段一致性校验"""
        # 期望不规划，但又指定节点数 → 语义冲突
        if not self.expected_needs_planning and self.expected_plan_nodes is not None:
            logger.warning(
                "[Dataset] case %s: expected_needs_planning=False 但设置了 "
                "expected_plan_nodes=%s，两者语义冲突，已忽略 expected_plan_nodes",
                self.id, self.expected_plan_nodes,
            )
            self.expected_plan_nodes = None

        # 复杂度取值校验
        if self.complexity not in ("simple", "medium", "complex"):
            logger.warning(
                "[Dataset] case %s: 非法 complexity=%s，降级为 medium",
                self.id, self.complexity,
            )
            self.complexity = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "input": self.input,
            "expected_output": self.expected_output,
            "category": self.category,
            "complexity": self.complexity,
            "expected_plan_nodes": self.expected_plan_nodes,
            "expected_needs_planning": self.expected_needs_planning,
            "metadata": self.metadata,
        }


class DatasetManager:
    """管理评估数据集"""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = (
            Path(data_dir) if data_dir else Path(__file__).parent / "data"
        )
        # ✅ P1-3 修复：parents=True 支持多层目录
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._datasets: Dict[str, List[TestCase]] = {}

    def load_dataset(
        self,
        name: str,
        file_format: str = "json",
        force_reload: bool = False,
    ) -> List[TestCase]:
        """
        从文件加载数据集。

        ✅ P1-1 修复：加 force_reload 参数，默认走缓存。
           原实现每次都重新读文件，但缓存不更新——语义不一致。

        ✅ P1-4 修复：format → file_format，避免遮蔽内置 format。
        """
        # 缓存命中
        if not force_reload and name in self._datasets:
            logger.debug("[Dataset] %s 缓存命中 (%d cases)",
                         name, len(self._datasets[name]))
            return self._datasets[name]

        file_path = self.data_dir / f"{name}.{file_format}"
        if not file_path.exists():
            logger.warning("[Dataset] %s 不存在于 %s，返回空列表",
                           name, file_path)
            return []

        with open(file_path, "r", encoding="utf-8") as f:
            if file_format == "json":
                raw = json.load(f)
            elif file_format in ("yaml", "yml"):
                raw = yaml.safe_load(f)
            else:
                raise ValueError(f"Unsupported format: {file_format}")

        # 兼容 {"cases": [...]} 包装
        if isinstance(raw, dict) and "cases" in raw:
            raw = raw["cases"]

        # ✅ P0-2 修复：显式校验 raw 是 list
        if not isinstance(raw, list):
            logger.error(
                "[Dataset] %s 顶层结构不是 list，实际 type=%s，返回空列表",
                name, type(raw).__name__,
            )
            return []

        if not raw:
            logger.warning("[Dataset] %s 加载成功但为空", name)
            self._datasets[name] = []
            return []

        cases: List[TestCase] = []
        seen_ids: set[str] = set()

        # ✅ P0-1 修复：记录 idx + id，让失败可定位
        for idx, item in enumerate(raw):
            # 非 dict 项直接跳过
            if not isinstance(item, dict):
                logger.error(
                    "[Dataset] %s 第 %d 条不是 dict，已跳过: type=%s, value=%s",
                    name, idx, type(item).__name__, str(item)[:80],
                )
                continue

            try:
                case_id = item.get("id") or f"case_{idx}"

                # ✅ P1-2：id 重复检测
                if case_id in seen_ids:
                    logger.warning(
                        "[Dataset] %s 第 %d 条 id=%s 重复，已跳过",
                        name, idx, case_id,
                    )
                    continue
                seen_ids.add(case_id)

                case = TestCase(
                    id=case_id,
                    input=item["input"],
                    expected_output=item.get("expected_output"),
                    category=item.get("category", "general"),
                    complexity=item.get("complexity", "medium"),
                    expected_plan_nodes=item.get("expected_plan_nodes"),
                    expected_needs_planning=item.get("expected_needs_planning", True),
                    metadata=item.get("metadata", {}) or {},
                )
                cases.append(case)

            except KeyError as e:
                logger.error(
                    "[Dataset] %s 第 %d 条 (id=%s) 缺少必需字段 %s，已跳过",
                    name, idx, item.get("id", "<无 id>"), e,
                )
            except (ValueError, TypeError) as e:
                logger.error(
                    "[Dataset] %s 第 %d 条 (id=%s) 字段类型错误，已跳过: %s",
                    name, idx, item.get("id", "<无 id>"), e,
                )

        if len(cases) < len(raw):
            logger.warning(
                "[Dataset] %s 共 %d 条，成功加载 %d 条，跳过 %d 条",
                name, len(raw), len(cases), len(raw) - len(cases),
            )

        self._datasets[name] = cases
        logger.info("[Dataset] 从 %s 加载 %d 条 case", name, len(cases))
        return cases

    def register_dataset(self, name: str, cases: List[TestCase]) -> None:
        """注册内存数据集（用于动态生成）"""
        self._datasets[name] = cases
        logger.debug("[Dataset] 注册内存数据集 %s (%d cases)", name, len(cases))

    def get_dataset(self, name: str) -> List[TestCase]:
        return self._datasets.get(name, [])

    def split_by_category(
        self, cases: List[TestCase],
    ) -> Dict[str, List[TestCase]]:
        """按类别切分"""
        splits: Dict[str, List[TestCase]] = {}
        for case in cases:
            splits.setdefault(case.category, []).append(case)
        return splits

    def split_by_complexity(
        self, cases: List[TestCase],
    ) -> Dict[str, List[TestCase]]:
        """按复杂度切分"""
        splits: Dict[str, List[TestCase]] = {}
        for case in cases:
            splits.setdefault(case.complexity, []).append(case)
        return splits

    def save_dataset(
        self, name: str, cases: List[TestCase], file_format: str = "json",
    ) -> Path:
        """✅ P2-2 补：把 TestCase 列表写回磁盘"""
        file_path = self.data_dir / f"{name}.{file_format}"
        payload = {"cases": [c.to_dict() for c in cases]}

        with open(file_path, "w", encoding="utf-8") as f:
            if file_format == "json":
                json.dump(payload, f, ensure_ascii=False, indent=2)
            elif file_format in ("yaml", "yml"):
                yaml.safe_dump(payload, f, allow_unicode=True)
            else:
                raise ValueError(f"Unsupported format: {file_format}")

        logger.info("[Dataset] 已保存 %d cases 到 %s", len(cases), file_path)
        return file_path
