"""
test_redis_session_store.py — RedisSessionStore 集成测试

所有测试连接真实 Redis 实例运行。
本地开发：确保 Redis 在 localhost:6379 可达
CI：由 integration job 的 Redis service 提供

运行：
    pytest tests/test_package/test_redis_session_store.py -v -m integration
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from task_planner.infrastructure.session_store import RedisSessionStore, SessionStore

redis = pytest.importorskip("redis.asyncio", reason="需要 redis 包")


class FakeSession:
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.start_time = time.time()


def _new_prefix() -> str:
    """每个测试独立 prefix，彻底隔离，绝不互相干扰。"""
    return f"test:{uuid.uuid4().hex}:"


async def _cleanup(client, prefix: str) -> None:
    """按 prefix 精确清理，不留残留。"""
    try:
        keys = await client.keys(f"{prefix}*")
        if keys:
            await client.delete(*keys)
    except Exception:
        pass


@pytest.fixture
async def store():
    """每个测试独立的 RedisSessionStore，用 DB 15 避免污染。"""
    prefix = _new_prefix()
    s = RedisSessionStore(
        "redis://localhost:6379/15",
        key_prefix=prefix,
        default_ttl=3600,
    )
    yield s
    await _cleanup(s._client, prefix)
    await s.close()


# ═══════════════════════════════════════════════════════════════════
# 集成测试：全部用真 Redis
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.integration
@pytest.mark.asyncio
async def test_implements_protocol(store):
    assert isinstance(store, SessionStore)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_and_get(store):
    session = FakeSession("t1")
    await store.set("t1", session)
    got = await store.get("t1")
    assert got is not None
    assert got.thread_id == "t1"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get("nonexistent") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_removes(store):
    session = FakeSession("t1")
    await store.set("t1", session)
    popped = await store.pop("t1")
    assert popped.thread_id == "t1"
    assert await store.get("t1") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_missing_returns_none(store):
    assert await store.pop("nonexistent") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_access(store):
    async def writer(i: int):
        await store.set(f"t{i}", FakeSession(f"t{i}"))

    async def reader(i: int):
        await store.get(f"t{i}")

    await asyncio.gather(
        *[writer(i) for i in range(50)],
        *[reader(i) for i in range(50)],
    )
    for i in range(50):
        got = await store.get(f"t{i}")
        assert got is not None
        assert got.thread_id == f"t{i}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cleanup_stale_is_noop(store):
    await store.set("t1", object())
    assert await store.cleanup_stale(ttl=1) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ttl_expires(store):
    """1 秒 TTL，等它过期"""
    store._default_ttl = 1
    await store.set("t1", "x")
    assert await store.get("t1") == "x"
    await asyncio.sleep(1.2)
    assert await store.get("t1") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repeated_set_resets_ttl(store):
    """Redis 版重复 set 重置 TTL"""
    store._default_ttl = 2
    await store.set("t1", "v1")
    await asyncio.sleep(1.1)
    await store.set("t1", "v2")
    await asyncio.sleep(1.1)
    assert await store.get("t1") == "v2"
    await asyncio.sleep(1.2)
    assert await store.get("t1") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exists_and_count(store):
    assert await store.exists("t1") is False
    await store.set("t1", "x")
    await store.set("t2", "y")
    assert await store.exists("t1") is True
    # prefix 唯一 → 只能看到本测试写的 2 个 key
    assert await store.count() == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_len_method(store):
    with pytest.raises(TypeError):
        len(store)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_contains_method(store):
    with pytest.raises(TypeError):
        _ = "t1" in store


@pytest.mark.integration
@pytest.mark.asyncio
async def test_custom_serializer():
    """用 JSON 序列化器"""
    import json

    prefix = _new_prefix()
    store = RedisSessionStore(
        "redis://localhost:6379/15",
        key_prefix=prefix,
        serialize=lambda x: json.dumps(x).encode("utf-8"),
        deserialize=lambda b: json.loads(b.decode("utf-8")),
    )
    try:
        await store.set("t1", {"foo": "bar", "n": 42})
        assert await store.get("t1") == {"foo": "bar", "n": 42}
    finally:
        await _cleanup(store._client, prefix)
        await store.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_key_prefix_isolation():
    """不同 prefix 互不干扰"""
    prefix_a = _new_prefix() + "a:"
    prefix_b = _new_prefix() + "b:"
    store_a = RedisSessionStore("redis://localhost:6379/15", key_prefix=prefix_a)
    store_b = RedisSessionStore("redis://localhost:6379/15", key_prefix=prefix_b)
    try:
        await store_a.set("t1", "from_a")
        await store_b.set("t1", "from_b")
        assert await store_a.get("t1") == "from_a"
        assert await store_b.get("t1") == "from_b"
    finally:
        for p, s in [(prefix_a, store_a), (prefix_b, store_b)]:
            await _cleanup(s._client, p)
            await s.close()
