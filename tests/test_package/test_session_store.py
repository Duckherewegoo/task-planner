"""
test_session_store.py — 锁定 P0-4 修复

原问题：_active_sessions 和 _session_store 双套并存，数据不一致，
       导致取消失败 + 会话永久泄漏。
修复：统一走 SessionStore 接口。

这些测试锁定的契约：
- get / set / pop 语义正确
- cleanup_stale 能清理过期会话
- 并发 set/get 不崩
"""
from __future__ import annotations

import asyncio
import time

import pytest

from task_planner.infrastructure.session_store import InMemorySessionStore


class FakeSession:
    """测试替身"""
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.start_time = time.time()


@pytest.fixture
def store():
    return InMemorySessionStore()


@pytest.mark.asyncio
async def test_set_and_get(store):
    session = FakeSession("t1")
    await store.set("t1", session)
    got = await store.get("t1")
    assert got is session


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get("nonexistent") is None


@pytest.mark.asyncio
async def test_pop_removes(store):
    session = FakeSession("t1")
    await store.set("t1", session)
    popped = await store.pop("t1")
    assert popped is session
    assert await store.get("t1") is None


@pytest.mark.asyncio
async def test_pop_missing_returns_none(store):
    assert await store.pop("nonexistent") is None


@pytest.mark.asyncio
async def test_cleanup_stale():
    store = InMemorySessionStore()
    # 显式指定"1 小时前创建"，模拟旧会话
    await store.set("t1", object(), created_at=time.time() - 3600)
    cleaned = await store.cleanup_stale(ttl=1)
    assert cleaned == 1


@pytest.mark.asyncio
async def test_concurrent_access(store):
    """并发 set / get / pop 不崩，语义一致"""
    async def writer(i: int):
        s = FakeSession(f"t{i}")
        await store.set(f"t{i}", s)

    async def reader(i: int):
        await store.get(f"t{i}")

    await asyncio.gather(
        *[writer(i) for i in range(50)],
        *[reader(i) for i in range(50)],
    )
    # 50 个 session 都在
    for i in range(50):
        got = await store.get(f"t{i}")
        assert got is not None
        assert got.thread_id == f"t{i}"
