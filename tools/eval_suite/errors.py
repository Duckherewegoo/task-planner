"""errors.py — eval_suite 异常体系。

不再到处用 except Exception，而是有明确的异常类型。
"""


class EvalSuiteError(Exception):
    """评估套件基础异常。"""


class GoldenGenerationError(EvalSuiteError):
    """黄金标准生成失败。"""


class GoldenValidationError(EvalSuiteError):
    """黄金标准结构验证失败。"""


class AgentRunError(EvalSuiteError):
    """Agent 执行失败。"""


class JudgeError(EvalSuiteError):
    """单评委调用/解析失败。"""


class JuryAggregationError(EvalSuiteError):
    """评委团聚合失败（如 UNANIMOUS 策略下评委不一致）。"""


class HumanReviewError(EvalSuiteError):
    """HIL 人工审查相关的失败。"""


__all__ = [
    "EvalSuiteError",
    "GoldenGenerationError",
    "GoldenValidationError",
    "AgentRunError",
    "JudgeError",
    "JuryAggregationError",
    "HumanReviewError",
]
