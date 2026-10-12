"""_executor.py 的类型归一与截断行为。"""
import types
from unittest.mock import AsyncMock

import pytest

from task_planner.core.graph.nodes._executor import (
    _DETAIL_MAX_LEN,
    _coerce_node_id,
    _coerce_text,
    execute_single_node,
)


# ── _coerce_node_id ──

@pytest.mark.parametrize("raw, expected", [
    (5, 5),
    (0, 0),
    (-1, -1),
    ("abc", "abc"),
    ("", ""),
    (True, "True"),    # bool 防御：True 不应被当成 int 5
    (False, "False"),
    (None, "None"),    # 已知行为，非理想但一致
    (3.14, "3.14"),
    ([1, 2], "[1, 2]"),
])
def test_coerce_node_id(raw, expected):
    assert _coerce_node_id(raw) == expected


def test_coerce_node_id_bool_is_not_int():
    """核心断言：True 不应被当成 int 1 处理。"""
    result = _coerce_node_id(True)
    assert result == "True"
    assert result != 1


# ── _coerce_text ──

@pytest.mark.parametrize("raw, expected", [
    ("abc", "abc"),
    ("", ""),
    (None, ""),         # 关键：None → ""，不是 "None"
    (123, "123"),
    (True, "True"),
    (0, "0"),
    ([], "[]"),
])
def test_coerce_text(raw, expected):
    assert _coerce_text(raw) == expected


def test_coerce_text_none_does_not_produce_literal_none_string():
    """核心断言：None 不应变成字符串 "None"。"""
    assert _coerce_text(None) == ""
    assert _coerce_text(None) != "None"


# ── 整句截断 ──

async def test_unknown_exception_detail_truncated_whole_sentence(monkeypatch):
    """
    未知异常分支的 detail 应整句截断到 _DETAIL_MAX_LEN——
    包括前缀 "💥 未知异常: " 和类型名。
    """
    import task_planner.core.graph.nodes._executor as ex

    monkeypatch.setattr(
        ex,
        "_hub",
        types.SimpleNamespace(dev=types.SimpleNamespace(LLM_NODE_TIMEOUT=1)),
    )
    monkeypatch.setattr(ex, "render_template", lambda *a, **kw: "prompt")
    monkeypatch.setattr(
        ex,
        "execute_node_llm",
        AsyncMock(side_effect=ValueError("x" * 5000)),
    )

    result = await execute_single_node(
        node={"id": 1, "name": "n1"},
        user_input="u",
        intent={},
    )

    assert result["status"] == "failed"
    assert len(result["detail"]) <= _DETAIL_MAX_LEN


async def test_node_name_none_does_not_produce_none_string(monkeypatch):
    """node['name'] = None 时，name 应回落到 f"步骤{nid}"，而非字符串 "None"。"""
    import task_planner.core.graph.nodes._executor as ex

    monkeypatch.setattr(
        ex,
        "_hub",
        types.SimpleNamespace(dev=types.SimpleNamespace(LLM_NODE_TIMEOUT=1)),
    )
    monkeypatch.setattr(ex, "render_template", lambda *a, **kw: "prompt")
    monkeypatch.setattr(
        ex,
        "execute_node_llm",
        AsyncMock(return_value=(True, "done")),
    )

    result = await execute_single_node(
        node={"id": 1, "name": None},
        user_input="u",
        intent={},
    )
    assert result["node_name"] != "None"
    assert "1" in result["node_name"]  # 包含 id


async def test_node_id_bool_does_not_leak_into_result(monkeypatch):
    """node['id'] = True 时，返回的 node_id 应是 'True' 而非 True。"""
    import task_planner.core.graph.nodes._executor as ex

    monkeypatch.setattr(
        ex,
        "_hub",
        types.SimpleNamespace(dev=types.SimpleNamespace(LLM_NODE_TIMEOUT=1)),
    )
    monkeypatch.setattr(ex, "render_template", lambda *a, **kw: "prompt")
    monkeypatch.setattr(
        ex,
        "execute_node_llm",
        AsyncMock(return_value=(True, "done")),
    )

    result = await execute_single_node(
        node={"id": True, "name": "n"},
        user_input="u",
        intent={},
    )
    assert result["node_id"] == "True"
    assert result["node_id"] is not True
