"""
测试数据集管理器：加载、校验、切分数据集
支持 JSON/YAML 格式，包含 input, expected_output, metadata 等字段
"""

import json
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
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
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent / "data"
        self.data_dir.mkdir(exist_ok=True)
        self._datasets: Dict[str, List[TestCase]] = {}

    def load_dataset(self, name: str, format: str = "json") -> List[TestCase]:
        """从文件加载数据集"""
        file_path = self.data_dir / f"{name}.{format}"
        if not file_path.exists():
            logger.warning(f"Dataset {name} not found at {file_path}, using empty")
            return []

        with open(file_path, "r", encoding="utf-8") as f:
            if format == "json":
                raw = json.load(f)
            elif format in ("yaml", "yml"):
                raw = yaml.safe_load(f)
            else:
                raise ValueError(f"Unsupported format: {format}")

        if isinstance(raw, dict) and "cases" in raw:
            raw = raw["cases"]

        cases = []
        for item in raw:
            try:
                case = TestCase(
                    id=item.get("id", f"case_{len(cases)}"),
                    input=item["input"],
                    expected_output=item.get("expected_output"),
                    category=item.get("category", "general"),
                    complexity=item.get("complexity", "medium"),
                    expected_plan_nodes=item.get("expected_plan_nodes"),
                    expected_needs_planning=item.get("expected_needs_planning", True),
                    metadata=item.get("metadata", {}),
                )
                cases.append(case)
            except KeyError as e:
                logger.error(f"Invalid test case missing field: {e}")

        self._datasets[name] = cases
        logger.info(f"Loaded {len(cases)} cases from {name}")
        return cases

    def register_dataset(self, name: str, cases: List[TestCase]) -> None:
        """注册内存数据集（用于动态生成）"""
        self._datasets[name] = cases

    def get_dataset(self, name: str) -> List[TestCase]:
        return self._datasets.get(name, [])

    def split_by_category(self, cases: List[TestCase]) -> Dict[str, List[TestCase]]:
        """按类别切分"""
        splits = {}
        for case in cases:
            splits.setdefault(case.category, []).append(case)
        return splits

    def split_by_complexity(self, cases: List[TestCase]) -> Dict[str, List[TestCase]]:
        """按复杂度切分"""
        splits = {}
        for case in cases:
            splits.setdefault(case.complexity, []).append(case)
        return splits
