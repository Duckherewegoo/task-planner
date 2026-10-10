"""
test_redis_session_store.py — RedisSessionStore 契约测试

与 test_session_store.py 的差异：
  ✅ 用 fakeredis 模拟（不需要真 Redis）
  ✅ 检查 Redis 版的语义差异：
       - 重复 set 会重置 TTL（InMemory 版不重置）
       - cleanup_stale 是 no-op
       - 无 __len__ / __contains__（用 count/exists 代替）
  ✅ 环境无 redis 依赖时整个文件自动 skip

运行：
    pytest tests/test_package/test_redis_session_store.py -v

前置：
    pip install -e ".[redis,dev]"   # 装 redis + fakeredis

真 Redis 集成测试（可选）：
    pytest -m integration tests/test_package/test_redis_session_store.py
"""
from __future__ import annotations

import asyncio
import time

import pytest

# ── 依赖缺失时整个文件 skip（不是 error，也不影响 CI）──
redis = pytest.importorskip("redis.asyncio", reason="需要 redis 包")
fakeredis = pytest.importorskip("fakeredis.aioredis", reason="需要 fakeredis 包")

from task_planner.infrastructure.session_store import (  # noqa: E402
    RedisSessionStore, SessionStore)


# ═══════════════════════════════════════════════════════════════════
#  测试替身
# ═══════════════════════════════════════════════════════════════════
class FakeSession:
    """跟 test_session_store.py 一致的测试替身"""
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.start_time = time.time()


# ═══════════════════════════════════════════════════════════════════
#  Fixture：用 fakeredis 注入客户端（不连真 Redis）
# ═══════════════════════════════════════════════════════════════════
@pytest.fixture
def store():
    """
    每个测试独立的 RedisSessionStore。
    用 fakeredis 替换真实客户端——不联网、毫秒级。
    """
    s = RedisSessionStore("redis://fake", default_ttl=3600)
    # 直接注入 fake 客户端（跳过懒加载）
    s._client = fakeredis.FakeRedis(decode_responses=False)
    yield s
    # 清理（fakeredis 在进程内，不用真 close）


# ═══════════════════════════════════════════════════════════════════
#  契约一致性：跟 InMemory 版行为相同
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_implements_protocol(store):
    """结构匹配 SessionStore Protocol"""
    assert isinstance(store, SessionStore)


@pytest.mark.asyncio
async def test_set_and_get(store):
    session = FakeSession("t1")
    await store.set("t1", session)
    got = await store.get("t1")
    # ⚠️ Redis 版经过序列化往返——不是同一个对象，但内容相同
    assert got is not session
    assert got.thread_id == "t1"


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get("nonexistent") is None


@pytest.mark.asyncio
async def test_pop_removes(store):
    session = FakeSession("t1")
    await store.set("t1", session)
    popped = await store.pop("t1")
    assert popped.thread_id == "t1"
    assert await store.get("t1") is None


@pytest.mark.asyncio
async def test_pop_missing_returns_none(store):
    assert await store.pop("nonexistent") is None


@pytest.mark.asyncio
async def test_concurrent_access(store):
    """并发 set / get 不崩，语义一致"""
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


# ═══════════════════════════════════════════════════════════════════
#  Redis 版特有语义
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_cleanup_stale_is_noop(store):
    """Redis 版 cleanup_stale 永远返回 0——依赖原生 TTL"""
    await store.set("t1", object())
    assert await store.cleanup_stale(ttl=1) == 0


@pytest.mark.asyncio
async def test_created_at_shortens_ttl(store):
    """
    created_at 指定"1 小时前创建" → 剩余 TTL 应小于 default_ttl。
    default_ttl=3600，指定 1 小时前创建 → 剩余约 1 秒。
    """
    client = store._client
    await store.set("t1", "x", created_at=time.time() - 3600)
    # 用真 client 查 TTL（fakeredis 支持 ttl）
    ttl = await client.ttl(store._key("t1"))
    # 应该接近 1（1 秒左右）
    assert 0 < ttl <= 5


@pytest.mark.asyncio
async def test_ttl_expires(store):
    """1 秒 TTL，1.2 秒后过期"""
    store._default_ttl = 1
    await store.set("t1", "x")
    assert await store.get("t1") == "x"
    await asyncio.sleep(1.2)
    assert await store.get("t1") is None


@pytest.mark.asyncio
async def test_repeated_set_resets_ttl(store):
    """
    ⚠️ 与 InMemory 版的语义差异：
       Redis 版重复 set 会「重置 TTL」为 default_ttl。
       InMemory 版重复 set 保留原 created_at。
    """
    store._default_ttl = 2

    await store.set("t1", "v1")
    await asyncio.sleep(1.1)

    # InMemory 会保留原时间；Redis 会刷新
    await store.set("t1", "v2")
    await asyncio.sleep(1.1)

    # Redis 版：TTL 被重置，1.1 秒后还没过期
    assert await store.get("t1") == "v2"

    # 再等 1.2 秒——才过期（因为 TTL 是 2 秒）
    await asyncio.sleep(1.2)
    assert await store.get("t1") is None


@pytest.mark.asyncio
async def test_exists(store):
    """Redis 版用 exists 代替 __contains__"""
    assert await store.exists("t1") is False
    await store.set("t1", "x")
    assert await store.exists("t1") is True


@pytest.mark.asyncio
async def test_count(store):
    """Redis 版用 count 代替 __len__"""
    assert await store.count() == 0
    await store.set("t1", "x")
    await store.set("t2", "y")
    assert await store.count() == 2


@pytest.mark.asyncio
async def test_no_len_method(store):
    """
    显式断言：Redis 版不提供 __len__。
    （这是设计选择——Redis 操作是异步的）
    """
    with pytest.raises(TypeError):
        len(store)


@pytest.mark.asyncio
async def test_no_contains_method(store):
    """
    显式断言：Redis 版不提供 __contains__。
    """
    with pytest.raises(TypeError):
        "t1" in store


# ═══════════════════════════════════════════════════════════════════
#  边界情况
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_serialize_failure_raises_typeerror(store):
    """session 无法序列化时抛 TypeError"""
    class Unserializable:
        def __reduce__(self):
            raise TypeError("cannot pickle")

    with pytest.raises(TypeError, match="无法序列化"):
        await store.set("t1", Unserializable())


@pytest.mark.asyncio
async def test_deserialize_corrupted_returns_none(store):
    """Redis 里的数据损坏 → get 返回 None（不崩）"""
    # 直接往 Redis 里塞垃圾
    await store._client.set(store._key("t1"), b"not-a-valid-pickle")
    assert await store.get("t1") is None


@pytest.mark.asyncio
async def test_custom_serializer():
    """用 JSON 序列化器——session 必须是 JSON-able"""
    import json
    store = RedisSessionStore(
        "redis://fake",
        serialize=lambda x: json.dumps(x).encode("utf-8"),
        deserialize=lambda b: json.loads(b.decode("utf-8")),
    )
    store._client = fakeredis.FakeRedis(decode_responses=False)

    await store.set("t1", {"foo": "bar", "n": 42})
    assert await store.get("t1") == {"foo": "bar", "n": 42}


@pytest.mark.asyncio
async def test_key_prefix_isolation():
    """不同 prefix 的 store 互不干扰"""
    store_a = RedisSessionStore("redis://fake", key_prefix="app_a:")
    store_b = RedisSessionStore("redis://fake", key_prefix="app_b:")
    shared_client = fakeredis.FakeRedis(decode_responses=False)
    store_a._client = shared_client
    store_b._client = shared_client

    await store_a.set("t1", "from_a")
    await store_b.set("t1", "from_b")

    assert await store_a.get("t1") == "from_a"
    assert await store_b.get("t1") == "from_b"


# ═══════════════════════════════════════════════════════════════════
#  真 Redis 集成测试（默认 skip）
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_redis_roundtrip():
    """
    真 Redis 集成测试。

    运行条件：
        - 本地 6379 有 Redis
        - pytest -m integration 显式启用
    """
    # ── 先探测 Redis 是否可达 ──
    from redis.asyncio import Redis
    from redis.exceptions import RedisError

    probe = Redis.from_url("redis://localhost:6379/15")
    try:
        await probe.ping()
    except (RedisError, OSError) as e:
        await probe.aclose() if hasattr(probe, "aclose") else await probe.close()
        pytest.skip(f"本地 Redis 不可达：{e}")
    finally:
        # 保证关闭
        try:
            if hasattr(probe, "aclose"):
                await probe.aclose()
            else:
                await probe.close()
        except Exception:
            pass

    # ── 真跑 ──
    store = RedisSessionStore("redis://localhost:6379/15")
    try:
        await store.set("test_real", {"a": 1})
        assert await store.get("test_real") == {"a": 1}
        await store.pop("test_real")
    finally:
        await store.close()
