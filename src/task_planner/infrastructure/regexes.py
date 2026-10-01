"""
regexes.py — 全项目预编译正则。
"""
import re

# ── 思考标签 ──
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# ── 模板未替换占位符 ──
UNREPLACED_PATTERN = re.compile(r"\$\{(\w+)\}|(?<!\$)\$(\w+)")

# ── 跳过规划 ──
SKIP_PLANNING_PATTERNS = [
    re.compile(r"不[需要]?(?:要|用).{0,6}(?:流程图|DAG|图表|拆解|步骤|规划)"),
    re.compile(
        r"(?:直接|只要|请).{0,8}(?:告诉|教|回答|给出|说明).{0,10}(?:方法|策略|建议|结论|做法|答案)"
    ),
    re.compile(r"(?:跳过|别|莫).{0,4}(?:规划|画图|拆解|流程)"),
    re.compile(r"不[需要]?(?:要|用).{0,4}画.{0,4}图"),
]

# ── 敏感信息脱敏 ──
SENSITIVE_PATTERNS_CONFIG = [
    {"pattern": r"sk-[a-zA-Z0-9]{20,}",         "replacement": "[REDACTED_API_KEY]"},
    {"pattern": r"AKIA[A-Z0-9]{16,}",           "replacement": "[REDACTED_AWS_KEY]"},
    {"pattern": r"ghp_[a-zA-Z0-9]{36,}",        "replacement": "[REDACTED_GITHUB_TOKEN]"},
    {"pattern": r"xox[bpr]-[a-zA-Z0-9-]+",      "replacement": "[REDACTED_SLACK_TOKEN]"},
    {"pattern": (r"(?<![a-zA-Z_])password\s*[:=]\s*\S{1,200}", re.IGNORECASE),
     "replacement": "password=[REDACTED]"},
    {"pattern": (r"(?<![a-zA-Z_])token\s*[:=]\s*\S{1,200}", re.IGNORECASE),
     "replacement": "token=[REDACTED]"},
    {"pattern": (r"(?<![a-zA-Z_])api_key\s*[:=]\s*\S{1,200}", re.IGNORECASE),
     "replacement": "api_key=[REDACTED]"},
    {"pattern": (r"(?<![a-zA-Z_])secret\s*[:=]\s*\S{1,200}", re.IGNORECASE),
     "replacement": "secret=[REDACTED]"},
    {"pattern": r"\b1[3-9]\d{9}\b",             "replacement": "[REDACTED_PHONE]"},
    {"pattern": r"\b\d{17}[\dXx]\b",            "replacement": "[REDACTED_ID_CARD]"},
]

SENSITIVE_PATTERNS = [
    (
        re.compile(cfg["pattern"])
        if isinstance(cfg["pattern"], str)
        else re.compile(*cfg["pattern"]),
        cfg["replacement"],
    )
    for cfg in SENSITIVE_PATTERNS_CONFIG
]
