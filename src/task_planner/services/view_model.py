FRONTEND_FIELDS = (
    "task_id", "svg", "flowchart_html", "direct_response",
    "status_text", "steps", "error", "cancel_requested",
    "current_node_index", "node_results", "needs_planning", "view_mode",
    "nodes", "edges",
)

# 优化方案：预计算字段集合，避免重复元组遍历
_FRONTEND_KEYS = frozenset(FRONTEND_FIELDS)

def to_frontend_view(self) -> dict:
    # 如果 self 本身是 dict-like 对象，可直接用交集操作
    return {k: v for k, v in self.items() if k in _FRONTEND_KEYS}
