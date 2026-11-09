from typing import Any

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


def validate_node_elements(nodes: list, rid: str) -> None:
    """
    校验每个 node 是 dict 且有 id 字段。

    ⚠️ 为什么需要：plan.get("nodes") 的外层 list 校验通过后，
       元素仍可能是 str / None / list——refine_node 里 n.get("id")
       会在非 dict 元素上抛 AttributeError，错误信息与"规划失败"无关。
       在入口处 Fail Fast，让问题在 plan_node 就暴露。
    """
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            logger.error(
                "[Graph] plan_node: nodes[%d] 非 dict! type=%s raw=%s (req=%s)",
                i,
                type(n).__name__,
                str(n)[:200],
                rid,
            )
            raise ValueError(
                f"规划失败：nodes[{i}] 应为 dict，实际为 {type(n).__name__} (req={rid})"
            )
        if n.get("id") is None:
            logger.error(
                "[Graph] plan_node: nodes[%d] 缺少 id 字段! raw=%s (req=%s)",
                i,
                str(n)[:200],
                rid,
            )
            raise ValueError(f"规划失败：nodes[{i}] 缺少 id 字段 (req={rid})")


def validate_edge_elements(edges: list, rid: str) -> None:
    """
    校验每个 edge 是 dict 且有 from/to 字段。

    ⚠️ from/to 的语义：节点 id，在节点集合内应存在——但"存在性"
       校验留给 render/save 层（它们更清楚节点集合）。这里只做形状校验。
    """
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            logger.error(
                "[Graph] plan_node: edges[%d] 非 dict! type=%s raw=%s (req=%s)",
                i,
                type(e).__name__,
                str(e)[:200],
                rid,
            )
            raise ValueError(
                f"规划失败：edges[{i}] 应为 dict，实际为 {type(e).__name__} (req={rid})"
            )
        if "from" not in e or "to" not in e:
            logger.error(
                "[Graph] plan_node: edges[%d] 缺少 from/to! raw=%s (req=%s)",
                i,
                str(e)[:200],
                rid,
            )
            raise ValueError(f"规划失败：edges[{i}] 缺少 from/to 字段 (req={rid})")


def coerce_node_id(raw: Any, fallback: int) -> int | str:
    """
    与 _executor.py 的 _coerce_node_id 同策略——保证 NodeResult.node_id
    契约成立。非 int/str 时回落到 idx（合法 int）。
    """
    if isinstance(raw, (int, str)) and not isinstance(raw, bool):
        return raw
    return fallback


def coerce_pointer(raw: Any) -> int | str | None:
    """把外部传入的指针值归一化到 int | str | None。

    动机：workflow.py 的 target_node_index 可能来自 URL query / 前端 JSON，
    运行时类型不受 TypedDict 静态契约保证。

    归一化规则（显式，无 passthrough）：
      - None         → None（无指针）
      - bool         → None（bool 是 int 子类，但业务上不是合法指针；
                     按 int 处理会让 True 变成 index=1，静默错误）
      - int          → int
      - str          → 能 int() 则转 int，不能转则保留原 str（供 node["id"] 匹配）
      - float/其他   → None（降级，不 passthrough——下游 nodes[3.0] 会炸）

    返回类型严格限定为 int | str | None，下游 Pyright 可精确推导。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # 必须在 int 之前——bool ⊂ int
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return raw
    # float / list / dict / object → 无法作为指针，降级
    return None


def ids_equal(a: Any, b: Any) -> bool:
    """node["id"] 与用户传入指针是否等价（int/str 双兼容）。

    动机：_executor.py 里 node["id"] 来自 LLM 输出，可能为 int 或 str；
    用户指针同样可能是两者之一。"3" == 3 为 False，需归一化比较。
    """
    if a == b:
        return True
    return str(a) == str(b)


__all__ = [
    "validate_node_elements",
    "validate_edge_elements",
    "coerce_node_id",
    "coerce_pointer",
    "ids_equal",
]
