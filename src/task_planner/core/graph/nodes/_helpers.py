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
                i, type(n).__name__, str(n)[:200], rid,
            )
            raise ValueError(
                f"规划失败：nodes[{i}] 应为 dict，实际为 {type(n).__name__} (req={rid})"
            )
        if n.get("id") is None:
            logger.error(
                "[Graph] plan_node: nodes[%d] 缺少 id 字段! raw=%s (req=%s)",
                i, str(n)[:200], rid,
            )
            raise ValueError(
                f"规划失败：nodes[{i}] 缺少 id 字段 (req={rid})"
            )


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
                i, type(e).__name__, str(e)[:200], rid,
            )
            raise ValueError(
                f"规划失败：edges[{i}] 应为 dict，实际为 {type(e).__name__} (req={rid})"
            )
        if "from" not in e or "to" not in e:
            logger.error(
                "[Graph] plan_node: edges[%d] 缺少 from/to! raw=%s (req=%s)",
                i, str(e)[:200], rid,
            )
            raise ValueError(
                f"规划失败：edges[{i}] 缺少 from/to 字段 (req={rid})"
            )

__all__ = ["validate_node_elements","validate_edge_elements"]
