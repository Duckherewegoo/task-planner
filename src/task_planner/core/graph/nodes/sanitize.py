"""
sanitize.py — 横切关注点：脱敏、用户视图过滤
所有节点共用，不依赖其他 nodes 子模块。

── v4 ──
  P3-1：_FIELD_TYPE_MAP 从 SafeNodeView.__annotations__ 派生，
           消灭与真源的同步漂移；bool 防御逻辑改为对 type 集合判断，
           对元组类型同样生效。
  P3-2：模块加载时 assert 真源字段 == SafeNodeView 字段，
           配置不一致在 import 阶段就炸，而不是运行时静默降级。
  P3-3：非 dict 输入直接 return {}，去掉无意义的 cast。
  P3-4：审计日志补 input_len / pattern_hits 两个关键字段。
  P3-5：docstring 补"SENSITIVE_PATTERNS 顺序敏感、不短路"的契约。

⚠️ 完整变更历史见 CHANGELOG.md。
"""

import hashlib
from typing import Any, TypedDict, cast, get_args, get_origin

from task_planner.infrastructure.constants import USER_VISIBLE_NODE_FIELDS
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.regexes import SENSITIVE_PATTERNS

logger = get_logger(__name__)

# P2-3：模块加载时一次性转 frozenset。
_VISIBLE_FIELDS: frozenset[str] = frozenset(USER_VISIBLE_NODE_FIELDS)


class SafeNodeView(TypedDict, total=False):
    """
    面向前端的节点视图。

    所有字段可选——原始节点字段可能缺失或类型不符被过滤。
    消费方必须用 .get() 访问，不要假设字段一定存在。
    """
    id: int
    name: str
    details: str
    status: str
    result: str


# P3-1：从 SafeNodeView.__annotations__ 派生字段 → 期望类型。
# 好处：
#   1. 消灭 SafeNodeView 与字段映射之间的重复维护
#   2. 未来加字段只改 SafeNodeView 一处
#   3. 每个字段的期望类型以类型注解为准，不会走样
def _extract_expected_types() -> dict[str, tuple[type, ...]]:
    """
    从 SafeNodeView.__annotations__ 提取 {字段名: (期望类型...)}。

    TypedDict 的注解在运行时可见，值可能是：
      - 单个类型：      int
      - 泛型：          list[str]（用 get_origin 取 base）
      - Optional：      str | None（用 get_args 展开）
    """
    result: dict[str, tuple[type, ...]] = {}
    for field_name, annotation in SafeNodeView.__annotations__.items():
        origin = get_origin(annotation)
        if origin is None:
            # 单类型，如 int / str
            result[field_name] = (annotation,)
        else:
            # 泛型 / Union：用 get_args 拆开
            args = get_args(annotation)
            # Union 里 None 不是运行时类型，过滤掉
            concrete = tuple(a for a in args if isinstance(a, type))
            result[field_name] = concrete or (object,)
    return result


_FIELD_TYPE_MAP: dict[str, tuple[type, ...]] = _extract_expected_types()

# P3-2：模块加载时强校验真源与 SafeNodeView 字段一致。
# 配置漂移在 import 阶段就炸，而不是生产环境静默丢字段。
_DRIFT_MISSING_IN_VIEW = _VISIBLE_FIELDS - _FIELD_TYPE_MAP.keys()
_DRIFT_MISSING_IN_SOURCE = _FIELD_TYPE_MAP.keys() - _VISIBLE_FIELDS
if _DRIFT_MISSING_IN_VIEW or _DRIFT_MISSING_IN_SOURCE:
    raise RuntimeError(
        "[Sanitize] USER_VISIBLE_NODE_FIELDS / SafeNodeView 配置漂移："
        f" 真源有但 SafeNodeView 缺={sorted(_DRIFT_MISSING_IN_VIEW)}; "
        f" SafeNodeView 有但真源缺={sorted(_DRIFT_MISSING_IN_SOURCE)}. "
        "请同步两处字段清单。"
    )


def _type_matches(value: Any, expected_types: tuple[type, ...]) -> bool:
    """
    判断 value 是否匹配期望类型集合。

   ⚠️ bool 是 int 的子类——isinstance(True, int) 返回 True，
     但业务上 'id' 字段不该被 True/False 通过校验。
     因此对 bool 值采用更严的规则：
     仅当 expected_types 显式包含 bool 时才接受。
    """
    if isinstance(value, bool):
        return bool in expected_types

    return isinstance(value, expected_types)


def sanitize_node_for_user(node: dict[str, Any]) -> SafeNodeView:
    """
    过滤掉内部 debug/meta 字段，并强制字段类型。

    契约：
      - 入参非 dict → 返回空 SafeNodeView 并 warn（不抛异常）
      - 字段名不在 USER_VISIBLE_NODE_FIELDS → 丢弃
      - 字段名在白名单但值类型不符 → 丢弃并 warn（不 cast、不转换）
      - 保留字段的原始值不做二次加工

    Raises:
        无。所有异常路径降级为"丢弃字段 + warn"。
    """
    if not isinstance(node, dict):
        logger.warning(
            "[Sanitize] sanitize_node_for_user received %r, returning empty view",
            type(node).__name__,
        )
        # P3-3：total=False 的空 dict 天然兼容 SafeNodeView，无需 cast
        return {}

    view: dict[str, Any] = {}
    for k, v in node.items():
        if k not in _VISIBLE_FIELDS:
            continue
        expected = _FIELD_TYPE_MAP.get(k)
        if expected is None:
            # 理论上不可达——模块加载时已 assert 字段一致。保底防御。
            logger.warning(
                "[Sanitize] field %r in visible list but missing type map, dropped",
                k,
            )
            continue
        if not _type_matches(v, expected):
            logger.warning(
                "[Sanitize] field %r expected %s, got %s, dropped",
                k, expected, type(v).__name__,
            )
            continue
        view[k] = v

    return cast(SafeNodeView, view)


def sanitize_nodes_for_user(nodes: list[dict[str, Any]]) -> list[SafeNodeView]:
    """批量脱敏。非 list 输入返回空列表。"""
    if not isinstance(nodes, list):
        logger.warning(
            "[Sanitize] sanitize_nodes_for_user received %r, returning empty list",
            type(nodes).__name__,
        )
        return []
    return [sanitize_node_for_user(n) for n in nodes]


def sanitize_input(text: str, *, audit_log: bool = True) -> str:
    """
    敏感信息脱敏。

    契约：
      - 入参非 str → 返回空字符串并 warn（不抛异常）
      - ⚠️ 顺序敏感：SENSITIVE_PATTERNS 顺序决定替换优先级。
        不短路——前一条的替换结果若命中后一条，会继续替换。
        改真源顺序可能改变最终文本，请谨慎。
      - audit_log=True 且确有替换时打审计日志：
          只记 SHA256 / 原文长度 / 脱敏后长度 / 命中数，不记原文

    Args:
        text: 待脱敏文本
        audit_log: 是否在发生替换时打审计日志

    Returns:
        脱敏后的字符串（无替换时原样返回）
    """
    if not isinstance(text, str):
        logger.warning(
            "[Sanitize] sanitize_input received %r, returning empty string",
            type(text).__name__,
        )
        return ""

    sanitized = text
    hits = 0
    for pattern, replacement in SENSITIVE_PATTERNS:
        new_text, n = pattern.subn(replacement, sanitized)
        if n:
            hits += n
            sanitized = new_text

    # P2-4 / P3-4：惰性计算 hash，且日志补 input_len / pattern_hits
    if audit_log and hits:
        original_hash = hashlib.sha256(text.encode()).hexdigest()
        logger.info(
            "[Audit] Input redacted | hash=%s | input_len=%d | sanitized_len=%d | hits=%d",
            original_hash,
            len(text),
            len(sanitized),
            hits,
        )
    return sanitized


__all__ = [
    "SafeNodeView",
    "sanitize_node_for_user",
    "sanitize_nodes_for_user",
    "sanitize_input",
]
