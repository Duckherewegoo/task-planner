"""
test_graph_finished.py — 锁定图终态判断逻辑

_is_graph_finished 决定 astream 循环是否提前 break。
判断错误会导致：
- 提前 break：前端拿不到完整进度
- 不 break：流式接口悬挂
"""
from __future__ import annotations

import pytest

from task_planner.services.agent.view import is_graph_finished as _is_graph_finished


class TestIsGraphFinished:

    def test_cancel_requested_is_finished(self):
        assert _is_graph_finished({"cancel_requested": True}, "values") is True

    def test_error_is_finished(self):
        assert _is_graph_finished({"error": "boom"}, "values") is True

    def test_index_beyond_nodes_is_finished(self):
        assert _is_graph_finished(
            {"nodes": [{"id": 1}], "current_node_index": 5}, "values"
        ) is True

    def test_direct_response_without_nodes_is_finished(self):
        assert _is_graph_finished(
            {"direct_response": "答案", "nodes": []}, "values"
        ) is True

    def test_direct_response_pending_not_finished(self):
        """哨兵值 __DIRECT_RESPONSE_PENDING__ 不算完成"""
        assert _is_graph_finished(
            {"direct_response": "__DIRECT_RESPONSE_PENDING__", "nodes": []},
            "values",
        ) is False

    def test_direct_response_with_nodes_not_finished(self):
        """有节点时，direct_response 不算终态"""
        assert _is_graph_finished(
            {"direct_response": "答案", "nodes": [{"id": 1}]}, "values"
        ) is False

    def test_normal_progress_not_finished(self):
        assert _is_graph_finished(
            {"nodes": [{"id": 1}, {"id": 2}], "current_node_index": 0}, "values"
        ) is False

    def test_updates_mode_never_finishes(self):
        """updates 模式永远返回 False，由 values 模式判断终态"""
        assert _is_graph_finished(
            {"cancel_requested": True}, "updates"
        ) is False

    def test_empty_event_not_finished(self):
        assert _is_graph_finished({}, "values") is False
