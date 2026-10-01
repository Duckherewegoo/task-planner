"""validator.py — 意图枚举校验（只纠偏，不兜底）"""
from __future__ import annotations

from task_planner.infrastructure.constants import (
    VALID_INTENT_CATEGORIES,
    VALID_INTENT_COMPLEXITIES,
)
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


def validate_intent(intent: dict, user_input: str) -> dict:
    """
    仅做枚举校验和非法值降级。
    不设置任何默认值——默认值统一由 recognize_intent 在 validate_intent 之后处理。
    返回浅拷贝，不修改入参。
    """
    validated = dict(intent)

    if validated.get("category") not in VALID_INTENT_CATEGORIES:
        logger.warning(
            "[Intent] 非法 category=%s, 降级为 other", validated.get("category")
        )
        validated["category"] = "other"

    if validated.get("complexity") not in VALID_INTENT_COMPLEXITIES:
        logger.warning(
            "[Intent] 非法 complexity=%s, 降级为 medium", validated.get("complexity")
        )
        validated["complexity"] = "medium"

    return validated


__all__ = ["validate_intent"]
