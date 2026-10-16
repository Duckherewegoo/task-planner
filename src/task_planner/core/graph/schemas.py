"""schemas.py — Pydantic 值对象

⚠️ 本文件不替代 TaskState：
   TaskState 保持 TypedDict——LangGraph 的 state 外壳不能换。
   Pydantic 只用于"有明确形状的值对象"，作为 state 里某个字段的值。

设计原则：
  - 边界处 model_validate(...).model_dump()——落 state 仍是纯 dict
  - extra="ignore"——LLM 多给字段不报错（LLM 输出不可信）
  - 枚举用 Literal——比 Enum 轻，model_dump() 直接是字符串

当前范围：仅 IntentModel（试点）。
后续若试点通过，可扩展 NodeResult / NodeExecResult / PlanModel 等。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

# ── 枚举（与 infrastructure/prompts/intent.txt 对齐）──
IntentCategory = Literal[
    "cooking", "engineering", "logistics", "learning",
    "life_admin", "creative", "consultation", "other",
]

IntentComplexity = Literal["simple", "medium", "complex"]


class IntentModel(BaseModel):
    """
    意图识别结果。

    来源：infrastructure/prompts/intent.txt 定义的输出 schema：
        {
            "needs_planning": boolean,
            "category": "cooking" | "engineering" | ...,
            "summary": string,
            "complexity": "simple" | "medium" | "complex"
        }

    消费方：
        - plan_node     读 category 决定 prompt 领域
        - routes        读 needs_planning 决定 plan / direct_answer
        - refine_node   读 category 传 refine_node_llm

    ⚠️ 字段与 prompt 严格对齐——改 prompt 时同步改这里。
    """

    model_config = ConfigDict(
        extra="ignore",   # LLM 可能多给字段——忽略而非报错
    )

    needs_planning: bool
    category: IntentCategory = "other"
    summary: str = ""
    complexity: IntentComplexity = "simple"


__all__ = ["IntentModel", "IntentCategory", "IntentComplexity"]
