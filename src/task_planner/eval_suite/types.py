"""
types.py — eval_suite 的所有数据类型。
用 dataclass / TypedDict 替代裸 dict，保证类型严谨。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


# ═══════════════════════════════════════════════════════════════════
#  枚举
# ═══════════════════════════════════════════════════════════════════
class VotingStrategy(Enum):
    """评委团聚合策略。"""
    MEDIAN = "median"          # 默认：抗离群值
    MEAN = "mean"              # 所有评委等权
    UNANIMOUS = "unanimous"    # 要求一致（高风险决策）
    WEIGHTED = "weighted"      # 按评委历史可靠性加权


class Difficulty(Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class CaseStatus(Enum):
    PENDING = "pending"        # 待审
    APPROVED = "approved"      # 人工通过
    REJECTED = "rejected"      # 人工拒绝
    FLAGGED = "flagged"        # 需进一步讨论


# ═══════════════════════════════════════════════════════════════════
#  黄金标准
# ═══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class GoldenCase:
    """一条黄金标准测试用例。"""
    case_id: str
    user_input: str
    expected_plan: dict[str, Any]          # {nodes: [...], edges: [...]}
    expected_intent: dict[str, Any]        # {needs_planning, category, ...}
    tags: tuple[str, ...] = ()
    difficulty: Difficulty = Difficulty.MEDIUM
    generated_by: str = ""                 # 生成该 case 的模型名
    validated: bool = False                # 是否通过程序化验证


# ═══════════════════════════════════════════════════════════════════
#  执行轨迹
# ═══════════════════════════════════════════════════════════════════
@dataclass
class AgentTrace:
    """一次 Agent 执行的完整轨迹。"""
    case_id: str
    user_input: str
    thread_id: str
    intent: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    node_states: dict[str, str] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    error: str | None = None
    mock_mode: bool = False


# ═══════════════════════════════════════════════════════════════════
#  评分结果
# ═══════════════════════════════════════════════════════════════════
@dataclass(frozen=True, slots=True)
class DimensionScore:
    """单个维度的评分。"""
    name: str
    score: float          # 0-10
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DeterministicScores:
    """确定性评分结果。"""
    intent_match: float = 0.0
    node_count_accuracy: float = 0.0
    edge_count_accuracy: float = 0.0
    structure_valid: bool = False
    node_name_overlap: float = 0.0

    @property
    def overall(self) -> float:
        weights = {
            "intent_match": 0.2, "node_count_accuracy": 0.2,
            "edge_count_accuracy": 0.15, "structure_valid": 0.2,
            "node_name_overlap": 0.25,
        }
        return round(sum(
            getattr(self, k) * w for k, w in weights.items()
        ), 3)


@dataclass(frozen=True, slots=True)
class JudgeScore:
    """单个评委的评分。"""
    judge_model: str
    dimensions: tuple[DimensionScore, ...] = ()
    overall_comment: str = ""
    raw_response: str = ""
    error: str | None = None

    @property
    def overall(self) -> float:
        if not self.dimensions:
            return 0.0
        return round(
            sum(d.score for d in self.dimensions) / len(self.dimensions), 2
        )

    def dimension(self, name: str) -> DimensionScore | None:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None


@dataclass(frozen=True)
class JuryResult:
    """评委团聚合结果。"""
    judge_scores: tuple[JudgeScore, ...] = ()
    aggregated_dimensions: dict[str, float] = field(default_factory=dict)
    aggregated_overall: float = 0.0
    agreement: float = 0.0              # 0-1，评委间一致性
    confidence_lower: float | None = None
    confidence_upper: float | None = None
    strategy: VotingStrategy = VotingStrategy.MEDIAN
    meta_review: str = ""


@dataclass
class CaseResult:
    """单个 case 的完整评估结果。"""
    case_id: str
    user_input: str
    difficulty: Difficulty
    duration_sec: float
    error: str | None
    mock_mode: bool
    deterministic: DeterministicScores
    jury: JuryResult | None = None
    human_review: HumanReview | None = None


@dataclass
class HumanReview:
    """HIL 人工审查记录。"""
    status: CaseStatus = CaseStatus.PENDING
    human_score: float | None = None
    human_comment: str = ""
    revised_suggestion: str = ""
