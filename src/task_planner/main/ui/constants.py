"""constants.py — 数据类 + 全局缓存 + 指纹计算"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Tuple,
)

from dash import no_update

from task_planner.infrastructure.cog import hub as _hub
from task_planner.utils.cytoscape_adapter import dag_to_cytoscape

_MAX_CACHE_INPUT_BYTES = _hub.dev.MAX_CACHE_INPUT_BYTES


# ═══════════════════════════════════════════════════════════════════
#  回调结果容器
# ═══════════════════════════════════════════════════════════════════
@dataclass
class HistorySelectResult:
    """
    on_history_select 回调返回容器。
    to_tuple() 顺序必须与 @callback 装饰器 Output 顺序一致。
    """
    elements: Any = None
    detail: Any = None
    pan: Any = None
    global_status: Any = None
    dag_store: Any = None
    replan_disabled: Any = True
    edit_disabled: Any = True
    resume_disabled: Any = True
    retry_disabled: Any = True
    action_status: Any = None
    history_dag_store: Any = None
    history_node_states: Any = None

    def to_tuple(self) -> tuple:
        return (
            self.elements if self.elements is not None else no_update,
            self.detail if self.detail is not None else no_update,
            self.pan if self.pan is not None else no_update,
            self.global_status if self.global_status is not None else no_update,
            self.dag_store if self.dag_store is not None else no_update,
            self.replan_disabled,
            self.edit_disabled,
            self.resume_disabled,
            self.retry_disabled,
            self.action_status if self.action_status is not None else no_update,
            self.history_dag_store if self.history_dag_store is not None else no_update,
            self.history_node_states if self.history_node_states is not None else no_update,
        )


@dataclass
class HistoryTapNodeResult:
    detail: Any = None
    selected_node: Any = None
    done_disabled: bool = True
    skip_disabled: bool = True
    fail_disabled: bool = True

    def to_tuple(self) -> tuple:
        return (
            self.detail if self.detail is not None else no_update,
            self.selected_node if self.selected_node is not None else no_update,
            self.done_disabled,
            self.skip_disabled,
            self.fail_disabled,
        )


# ═══════════════════════════════════════════════════════════════════
#  Cytoscape 元素缓存
# ═══════════════════════════════════════════════════════════════════
_cyto_cache: Dict[str, Any] = {"hash": None, "elements": None}
_cyto_lock = threading.Lock()


def _stable_fingerprint(
    nodes: List[dict],
    edges: List[dict],
    node_states: Dict[str, str],
) -> Tuple[str, int]:
    try:
        node_ids = tuple(sorted(str(n.get("id", i)) for i, n in enumerate(nodes)))
        edge_keys = tuple(
            sorted((str(e["from"]), str(e["to"]), e.get("label", "")) for e in edges)
        )
        topo_part = f"{len(nodes)}|{len(edges)}|{node_ids}|{edge_keys}"
        state_part = json.dumps(node_states, sort_keys=True, ensure_ascii=False)
        raw = f"{topo_part}||{state_part}"
        raw_bytes = raw.encode("utf-8")
        return hashlib.md5(raw_bytes).hexdigest(), len(raw_bytes)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"Cache fingerprint failed: node_states contains non-serializable value. "
            f"All values must be str. Got error: {exc}"
        ) from exc


def cytoscape_cached(
    nodes: List[dict],
    edges: List[dict],
    node_states: Dict[str, str],
) -> list:
    fingerprint, raw_size = _stable_fingerprint(nodes, edges, node_states)
    if raw_size > _MAX_CACHE_INPUT_BYTES:
        return dag_to_cytoscape(nodes, edges, node_states)
    with _cyto_lock:
        if (
            fingerprint == _cyto_cache["hash"]
            and _cyto_cache["elements"] is not None
        ):
            return _cyto_cache["elements"]
        elements = dag_to_cytoscape(nodes, edges, node_states)
        _cyto_cache["hash"] = fingerprint
        _cyto_cache["elements"] = elements
        return elements


__all__ = [
    "HistorySelectResult",
    "HistoryTapNodeResult",
    "cytoscape_cached",
]
