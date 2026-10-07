"""json_utils.py — LLM 输出 JSON 清洗 / 提取 / 归一化"""
from __future__ import annotations

import json
import re
from json import JSONDecoder

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)

_JSON_DECODER = JSONDecoder()


# ═══════════════════════════════════════════════════════════════
#  尾部 JSON 提取（用于"思考过程 + JSON" 混合输出）
# ═══════════════════════════════════════════════════════════════
def extract_tail_json(text: str) -> str | None:
    """
    从含纯文本思考过程的响应中提取尾部 JSON。
    策略：从最后的 '}' 反扫 '{'，用 raw_decode 解析，要求恰好覆盖到尾部。
    """
    if not text:
        return None

    last_rbrace = text.rfind("}")
    if last_rbrace == -1:
        return None
    target_end = last_rbrace + 1

    pos = target_end
    while True:
        start = text.rfind("{", 0, pos)
        if start == -1:
            return None
        try:
            obj, end = _JSON_DECODER.raw_decode(text, start)
            if isinstance(obj, dict) and end == target_end:
                logger.debug("[LLM] 尾部JSON提取成功, len=%d", end - start)
                return text[start:end]
        except json.JSONDecodeError:
            pass
        pos = start


# ═══════════════════════════════════════════════════════════════
#  清洗：剥离 thinking / markdown / 尾部 JSON 提取
# ═══════════════════════════════════════════════════════════════
def clean_json_str(text: str) -> str:
    """更稳健的 JSON 提取：兼容 thinking 标签、纯文本思考、markdown 代码块"""
    if not text or not isinstance(text, str):
        return "{}"

    # 1. 基础清洗
    text = text.strip().lstrip("\ufeff")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

    # 2. 剥离 thinking 标签（从 regexes 拿预编译版本）
    from task_planner.infrastructure.regexes import THINK_RE

    _prev = text
    text = THINK_RE.sub("", text).strip()
    if not text:
        text = _prev
        logger.warning("[LLM] clean_json_str thinking标签剥离后文本为空，已回退")

    # 3. 剥离 Qwen 风格的纯文本思考过程
    if text.startswith("思考过程") or text.startswith("思考："):
        logger.debug("[LLM] clean_json_str 检测到纯文本思考前缀，启用尾部JSON提取")
        candidate = extract_tail_json(text)
        if candidate is not None:
            return candidate
        logger.warning("[LLM] clean_json_str 尾部JSON提取失败，进入后续降级解析")

    # 4. 去除 markdown 代码块
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE).strip()

    # 5. 直接解析
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError as e:
        logger.debug(
            "[LLM] clean_json_str 直接解析失败: %s | text前100字符: %s", e, text[:100]
        )

    # 6. 降级：找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError as e:
            logger.debug("[LLM] clean_json_str 降级解析也失败: %s", e)

    logger.warning("[LLM] clean_json_str 所有解析均失败 | 原始前300字符: %s", text[:300])
    return "{}"


def extract_json(raw: str) -> dict:
    """从 LLM 原始返回中提取 JSON dict"""
    logger.debug("[LLM] extract_json input (first 200 chars): %s", raw[:200])
    try:
        cleaned = clean_json_str(raw)
        if not cleaned or cleaned == "{}":
            if raw.strip().startswith("{") and "needs_planning" in raw:
                logger.error(
                    "[LLMClient] clean_json_str 丢失了有效JSON! raw前300字符: %s",
                    raw[:300],
                )
            return {}
        result = json.loads(cleaned)
        if isinstance(result, dict):
            logger.debug("[LLM] extract_json 成功, keys=%s", list(result.keys()))
        else:
            logger.warning(
                "[LLM] extract_json 返回值非dict: type=%s", type(result).__name__
            )
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError as e:
        logger.warning(
            "[LLMClient] JSON 解析失败: %s | 原始前200字符: %s", e, raw[:200]
        )
        return {}
    except Exception as e:
        logger.error(
            "[LLMClient] extract_json 非预期异常: %s: %s | 原始前200字符: %s",
            type(e).__name__,
            e,
            raw[:200],
        )
        return {}


# ═══════════════════════════════════════════════════════════════
#  归一化：多种上游格式 → 统一 schema
# ═══════════════════════════════════════════════════════════════
def normalize_llm_output(raw: dict, expected_keys: list[str]) -> dict:
    """
    兼容上游格式：
      {"result": "...", "notes": "..."}       (executor 风格)
      {"detail": "...", "meta": {...}}        (refine 单数风格)
      {"name": "...", "details": "...", ...}  (目标契约，快速路径)
    输出对齐 expected_keys（通常 name/details/meta）。
    """
    if not isinstance(raw, dict):
        logger.error(
            "[LLM] normalize_llm_output 输入非 dict, 已拦截: type=%s, value=%s",
            type(raw).__name__,
            str(raw)[:100],
        )
        return {}

    # 快速路径
    if all(k in raw for k in expected_keys):
        return raw

    # P1: {"result": "...", "notes": "..."}  →  name/details/meta
    if "result" in raw:
        result_val = raw["result"]
        if isinstance(result_val, str):
            meta = raw.get("meta", {})
            if not isinstance(meta, dict):
                logger.warning(
                    "[LLM] 'meta' 字段非 dict, 已重置为空字典: type=%s",
                    type(meta).__name__,
                )
                meta = {}
            if "notes" in raw:
                meta["notes"] = raw["notes"]
            return {
                "name": raw.get("name", ""),
                "details": result_val,
                "meta": meta,
            }
        else:
            logger.warning(
                "[LLM] 'result' 字段非字符串, 跳过 P1 适配: type=%s",
                type(result_val).__name__,
            )

    # P2: {"detail": "...", "meta": {...}}  →  name/details/meta
    if "detail" in raw:
        meta = raw.get("meta", {})
        if not isinstance(meta, dict):
            logger.warning(
                "[LLM] 'meta' 字段非 dict, 已重置为空字典: type=%s",
                type(meta).__name__,
            )
            meta = {}
        return {
            "name": raw.get("name", ""),
            "details": raw["detail"],
            "meta": meta,
        }

    # P3: 兜底
    logger.warning(
        "[LLM] normalize_llm_output 无法识别的输出格式: keys=%s", list(raw.keys())
    )
    return raw


__all__ = [
    "extract_tail_json",
    "clean_json_str",
    "extract_json",
    "normalize_llm_output",
]
