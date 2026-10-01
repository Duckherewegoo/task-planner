"""
loader.py — Prompt 加载器。
从 .txt 读取，返回 string.Template 实例。
"""
from pathlib import Path
from string import Template

_DIR = Path(__file__).resolve().parent   # 指向 prompts/ 本身


def _load(name: str) -> Template:
    path = _DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件缺失: {path}")
    return Template(path.read_text(encoding="utf-8"))


INTENT_PROMPT       = _load("intent")
PLANNER_PROMPT      = _load("planner")
NODE_REFINE_PROMPT  = _load("node_refine")
EXECUTE_NODE_PROMPT = _load("execute_node")

__all__ = [
    "INTENT_PROMPT",
    "PLANNER_PROMPT",
    "NODE_REFINE_PROMPT",
    "EXECUTE_NODE_PROMPT",
]
