"""
redis_session_store.py — 会话存储的 Redis 实现
═══════════════════════════════════════════════════════════════════════
与 InMemorySessionStore 的差异：

  ✅ 多 worker 友好 —— 会话状态在 Redis 共享，进程间可见
  ✅ 原生 TTL —— Redis 自动过期，无需手动清理
  ✅ 懒加载客户端 —— 首次使用时才建立连接

  ⚠️ 语义差异（与 InMemory 版）：
     1. 重复 set（不传 created_at）会「重置 TTL」为 default_ttl
        InMemory 版重复 set 会「保留原 created_at」不刷新
     2. cleanup_stale 是 no-op（返回 0）—— 依赖 Redis 原生 TTL
     3. 无 __len__ / __contains__（Redis 操作是异步的，无法实现同步方法）
        用 await store.count() / await store.exists(tid) 代替

  ⚠️ session 必须可序列化
     默认 pickle —— 能存任意 Python 对象
     不能存 asyncio.Event / asyncio.Queue / 文件句柄 / socket 等

  ⚠️ pickle 反序列化的安全风险
     pickle.loads 会执行任意代码。若 Redis 被入侵，攻击者可注入恶意数据。
     生产环境建议传 JSON 序列化器：
         RedisSessionStore(url,
             serialize=lambda x: orjson.dumps(x),
             deserialize=lambda b: orjson.loads(b))

依赖：
    pip install -e ".[redis]"

使用：
    store = RedisSessionStore("redis://localhost:6379/0")
    await store.set("t1", session)
    session = await store.get("t1")
    await store.close()   # 进程退出时调用

存储结构：
    key   = <key_prefix><tid>
    value = 序列化后的 session
    ttl   = default_ttl（或按 created_at 算剩余）
"""
from __future__ import annotations

import asyncio
import pickle
import time
from collections.abc import Callable
from typing import Any

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.infrastructure.redis_session_store")


# ═══════════════════════════════════════════════════════════════════
#  可选依赖：redis
# ═══════════════════════════════════════════════════════════════════
try:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    Redis = None  # type: ignore
    RedisError = Exception  # type: ignore


# ═══════════════════════════════════════════════════════════════════
#  RedisSessionStore
# ═══════════════════════════════════════════════════════════════════
class RedisSessionStore:
    """
    Redis 会话存储。

    结构匹配 SessionStore Protocol（不显式继承）。

    ⚠️ 见模块 docstring 的「语义差异」章节。
    """

    __slots__ = (
        "_url", "_prefix", "_default_ttl",
        "_serialize", "_deserialize",
        "_client", "_client_lock",
    )

    def __init__(
        self,
        url: str,
        *,
        key_prefix: str = "task_planner:session:",
        default_ttl: int = 3600,
        serialize: Callable[[Any], bytes] = pickle.dumps,
        deserialize: Callable[[bytes], Any] = pickle.loads,
    ) -> None:
        if not _HAS_REDIS:
            raise ImportError(
                "RedisSessionStore 需要 redis 客户端："
                "pip install -e \".[redis]\""
            )
        self._url = url
        self._prefix = key_prefix
        self._default_ttl = default_ttl
        self._serialize = serialize
        self._deserialize = deserialize
        self._client: Redis | None = None
        self._client_lock = asyncio.Lock()

    # ═══════════════════════════════════════════════════════════════
    #  内部：客户端懒加载 + key 拼接
    # ═══════════════════════════════════════════════════════════════
    async def _get_client(self) -> Redis:
        """双重检查锁——多协程首次并发只建一个客户端"""
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = Redis.from_url(
                    self._url,
                    decode_responses=False,   # 我们处理 bytes
                )
        return self._client

    def _key(self, tid: str) -> str:
        return f"{self._prefix}{tid}"

    # ═══════════════════════════════════════════════════════════════
    #  接口实现（与 SessionStore Protocol 结构匹配）
    # ═══════════════════════════════════════════════════════════════
    async def get(self, tid: str) -> Any | None:
        """读取会话。不存在或反序列化失败返回 None。"""
        client = await self._get_client()
        data = await client.get(self._key(tid))
        if data is None:
            return None
        try:
            return self._deserialize(data)
        except Exception as e:
            logger.error(
                "[RedisStore] 反序列化失败 tid=%s: %s", tid, e,
            )
            return None

    async def set(
        self,
        tid: str,
        session: Any,
        *,
        created_at: float | None = None,
    ) -> None:
        """
        写入会话。

        ⚠️ 与 InMemory 版差异：
           重复 set（不传 created_at）会「重置 TTL」为 default_ttl。
           如需保留原 TTL，请传 created_at=原时间，或改用 SET ... KEEPTTL。

        Args:
            tid:         会话 ID
            session:     任意可序列化对象
            created_at:  可选。指定创建时间（测试模拟旧会话用）。
                         不传时：用 default_ttl
                         传了时：用「default_ttl - 已过去的时间」作为剩余 TTL
        """
        client = await self._get_client()
        try:
            data = self._serialize(session)
        except Exception as e:
            raise TypeError(f"session 无法序列化：{e}") from e

        # ── 计算实际 TTL ──
        if created_at is not None:
            elapsed = time.time() - created_at
            ttl = max(1, self._default_ttl - int(elapsed))
        else:
            ttl = self._default_ttl

        # ── SET ... EX 原子写入 + 设过期 ──
        await client.set(self._key(tid), data, ex=ttl)

    async def pop(self, tid: str) -> Any | None:
        """取出并删除。不存在返回 None。"""
        client = await self._get_client()
        key = self._key(tid)

        # ── 优先 GETDEL（Redis 6.2+，原子操作）──
        data = None
        try:
            data = await client.getdel(key)
        except (AttributeError, RedisError):
            # 老版本 Redis / 客户端不支持——pipeline 兜底
            # 注：redis-py 的 Pipeline 本身不是并发场景，只需简单组合
            pipe = client.pipeline()
            pipe.get(key)
            pipe.delete(key)
            results = await pipe.execute()
            data = results[0]

        if data is None:
            return None
        try:
            return self._deserialize(data)
        except Exception as e:
            logger.error(
                "[RedisStore] 反序列化失败 tid=%s: %s", tid, e,
            )
            return None

    async def cleanup_stale(self, ttl: int) -> int:
        """
        Redis 版为 no-op —— Redis 原生 TTL 自动清理。

        ⚠️ 保留此方法仅为兼容 SessionStore 接口，永远返回 0。
           监控/日志时请注意：这里返回 0 不代表「没有清理」。

        参数 ttl 被忽略（TTL 已在 set 时设定）。
        """
        return 0

    # ═══════════════════════════════════════════════════════════════
    #  生命周期（非协议要求，但使用方应调用）
    # ═══════════════════════════════════════════════════════════════
    async def close(self) -> None:
        """
        关闭 Redis 连接。
        进程退出时调用；或在 health check 失败后重建客户端前调用。
        """
        if self._client is not None:
            try:
                # redis-py 5.0.1+ 用 aclose；老版本降级到 close
                if hasattr(self._client, "aclose"):
                    await self._client.aclose()
                else:
                    await self._client.close()  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("[RedisStore] 关闭失败: %s", e)
            finally:
                self._client = None

    # ═══════════════════════════════════════════════════════════════
    #  便利方法（替代 InMemory 版的 __len__ / __contains__）
    # ═══════════════════════════════════════════════════════════════
    async def count(self) -> int:
        """
        统计活跃会话数（用 SCAN 遍历）。

        ⚠️ 大 Redis 上慢——仅供健康检查 / 监控用。
           替代 InMemory 版的 len(store)。
        """
        client = await self._get_client()
        n = 0
        # decode_responses=False 时，pattern 传 str 也能用（内部会编码）
        async for _ in client.scan_iter(
            match=f"{self._prefix}*", count=100,
        ):
            n += 1
        return n

    async def exists(self, tid: str) -> bool:
        """
        检查会话是否存在。
        替代 InMemory 版的 `tid in store`。
        """
        client = await self._get_client()
        return bool(await client.exists(self._key(tid)))


__all__ = ["RedisSessionStore"]
