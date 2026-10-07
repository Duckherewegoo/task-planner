"""
loader.py — Prompt 加载器。
从 .txt 读取，返回 string.Template 实例。
"""
# ═══════════════════════════════════════════════════════════════════
#  模板渲染（供 llm_client / nodes 共用）
# ═══════════════════════════════════════════════════════════════════
import logging
from pathlib import Path
from string import Template

_logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent   # 指向 prompts/ 本身


def _load(name: str) -> Template:
    path = _DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件缺失: {path}")
    return Template(path.read_text(encoding="utf-8"))

def render_template(tpl: Template, req_id: str, **kwargs) -> str:
    """
    安全渲染 Prompt 模板。
    缺变量时不抛异常，用 safe_substitute 兜底并打 warning。
    """
    try:
        return tpl.substitute(**kwargs)
    except KeyError as e:
        _logger.warning("[Prompt] 缺少变量 %s (req=%s)，已降级为 safe_substitute", e, req_id)
        return tpl.safe_substitute(**kwargs)

INTENT_PROMPT       = _load("intent")
PLANNER_PROMPT      = _load("planner")
NODE_REFINE_PROMPT  = _load("node_refine")
EXECUTE_NODE_PROMPT = _load("execute_node")



__all__ = [
    "INTENT_PROMPT",
    "PLANNER_PROMPT",
    "NODE_REFINE_PROMPT",
    "EXECUTE_NODE_PROMPT",
    "render_template",
]
