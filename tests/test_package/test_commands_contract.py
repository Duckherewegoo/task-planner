# tests/test_package/test_commands_contract.py
"""commands.py 的 feedback 键名契约——与 execute.execute_node 的读取键名对齐。

这些测试不跑图、不调 LLM、不碰 DB——只验证"传出去的 resume_data 键名
是否与 execute 期望一致"。它们是主路径的第一道护栏。
"""

import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.types import Command

from task_planner.core.graph.state import UserAction
from task_planner.services.agent import commands

# ── 构造一个最小 session 替身 ──


def _make_session(thread_id="t-1"):
    s = MagicMock()
    s.thread_id = thread_id
    s.config = {"configurable": {"thread_id": thread_id}}
    s.cancel_event = MagicMock()
    s.cancel_event.set = MagicMock()
    s.cancel_event.is_set = MagicMock(return_value=False)
    s.cancel_event.clear = MagicMock()
    s._runner_task = None
    return s


def _patch_commands(monkeypatch, session):
    """把 commands 模块的外部依赖全 mock 掉，只留 '传参' 这段逻辑被观测。"""
    captured = {}

    async def _fake_ainvoke(cmd, config):
        captured["cmd"] = cmd
        captured["config"] = config

    fake_graph = types.SimpleNamespace(ainvoke=_fake_ainvoke)

    monkeypatch.setattr(commands, "graph", fake_graph)
    monkeypatch.setattr(
        commands,
        "get_session",
        AsyncMock(return_value=session),
    )
    # contextvar 相关的真实用法保留（不需要 mock）
    return captured


# ── modify_task ──


async def test_modify_task_sends_correct_feedback_keys(monkeypatch):
    """核心回归：modify 必须传 user_action / modified_input，不是 action / user_input。"""
    session = _make_session()
    captured = _patch_commands(monkeypatch, session)

    ok = await commands.modify_task("t-1", "新需求")

    assert ok is True
    cmd = captured["cmd"]
    assert isinstance(cmd, Command)
    resume = cmd.resume
    assert resume == {
        "user_action": UserAction.MODIFY,
        "modified_input": "新需求",
    }
    # 断言不存在旧键名
    assert "action" not in resume
    assert "user_input" not in resume


async def test_modify_task_rejects_empty_after_sanitize(monkeypatch):
    """sanitize 后为空 → 拒绝，不传 resume。"""
    session = _make_session()
    captured = _patch_commands(monkeypatch, session)

    ok = await commands.modify_task("t-1", "")  # 空串

    assert ok is False
    assert "cmd" not in captured


async def test_modify_task_rejects_non_str_input(monkeypatch):
    """sanitize_input 对非 str 返回 ''——同样视为拒绝。"""
    session = _make_session()
    captured = _patch_commands(monkeypatch, session)

    ok = await commands.modify_task("t-1", None)  # type: ignore

    assert ok is False
    assert "cmd" not in captured


async def test_modify_task_returns_false_when_no_session(monkeypatch):
    monkeypatch.setattr(commands, "get_session", AsyncMock(return_value=None))
    ok = await commands.modify_task("missing", "x")
    assert ok is False


# ── resume_task ──


async def test_resume_task_sends_correct_feedback_key(monkeypatch):
    session = _make_session()
    captured = _patch_commands(monkeypatch, session)

    ok = await commands.resume_task("t-1")
    assert ok is True
    resume = captured["cmd"].resume
    assert resume == {"user_action": UserAction.CONTINUE}


# ── retry_node_cmd ──


async def test_retry_node_cmd_sends_correct_feedback_key(monkeypatch):
    """核心回归：retry 必须传 user_action / retry_node_index，不是 action / node_index。"""
    session = _make_session()
    captured = _patch_commands(monkeypatch, session)

    ok = await commands.retry_node_cmd("t-1", node_index=3)
    assert ok is True
    resume = captured["cmd"].resume
    assert resume == {
        "user_action": UserAction.RETRY_NODE,
        "retry_node_index": 3,
    }
    assert "action" not in resume
    assert "node_index" not in resume


# ── cancel_task ──


async def test_cancel_task_sets_event(monkeypatch):
    session = _make_session()
    monkeypatch.setattr(commands, "get_session", AsyncMock(return_value=session))

    ok = await commands.cancel_task("t-1")
    assert ok is True
    session.cancel_event.set.assert_called_once()


async def test_cancel_task_returns_false_when_no_session(monkeypatch):
    monkeypatch.setattr(commands, "get_session", AsyncMock(return_value=None))
    ok = await commands.cancel_task("missing")
    assert ok is False
