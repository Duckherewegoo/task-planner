"""client.py — MongoDB 连接生命周期管理（同步，适配 Dash WSGI）

⚠️ 为什么从 Motor 异步换成 pymongo 同步：
   Dash 4.x 的 WSGI backend（dash/backends/ws.py:570）用 asyncio.run
   驱动每个 async 回调——每回调一个新 loop，跑完即毁。Motor 的
   AsyncIOMotorClient 绑定 loop，无法跨回调复用 → 每回调重建 →
   连接池爆炸或 "Event loop is closed"。

   Dash 的 worker 是同步线程——一个线程处理一个回调。异步在这个
   框架里没有跨请求收益。同步 pymongo 客户端不绑 loop，一次创建
   永久复用，在 Dash 的同步 worker 里是自然的。

⚠️ 关于"假 async"：
   core/db/ 下的业务函数（tasks.py / plans.py / nodes.py / _helpers.py）
   **保留 async def 签名**，但函数体内是同步调用。这是为了：
     - 调用方的 await 零改动
     - 未来切方案 C（后台常驻 loop）时，只改函数体不动签名
   本文件（client.py）是底层——不再需要 async，改成同步。

⚠️ 关于清理：
   GracefulShutdown.install() 在 debug 模式下会因 WERKZEUG_RUN_MAIN
   守卫而不注册信号处理器——必须同时挂 atexit 兜底。
   两处都调用 close_db()，所以它必须幂等。
"""

import atexit
import sys
import threading
import time

from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient
from pymongo.database import Database

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.core.db.client")

# ═══════════════════════════════════════════════════════════════════
#  模块级状态
# ═══════════════════════════════════════════════════════════════════
_client: MongoClient | None = None
_db: Database | None = None

# ✅ 并发保护：
#    pymongo 的 MongoClient 本身线程安全，但"检查 _client + 创建"这个
#    复合操作不是原子的。多 worker / 多线程场景下两个线程可能同时
#    通过 if _client is None 检查 → 各自建一个 client → 一个覆盖另
#    一个 → 旧连接泄漏。用 _init_lock 串行化初始化。
_init_lock = threading.Lock()

# 失败冷却——全局，与线程无关
_last_fail_time: float = 0.0
_FAIL_COOLDOWN = 10.0   # 失败后 10 秒内不再重试，防雪崩


def init_db() -> None:
    """
    同步初始化 MongoDB 连接，创建索引。

    ⚠️ 从 async def 改回 def——Motor 换成 pymongo 后无 loop 依赖，
       可以在任意上下文安全调用（包括模块 import 时、atexit 时）。

    ⚠️ 幂等 + 线程安全：
       - 已初始化时直接返回（快路径）
       - 双检锁：慢路径进入 _init_lock 后再检查一次
       - 异常时清理本地 new_client，不留半成品
    """
    global _client, _db
    if _client is not None:
        return
    with _init_lock:
        # double-check：可能等待锁期间别的线程已经初始化完
        if _client is not None:
            return

        # 先构造本地变量，全部成功后再赋给全局（防半成品泄漏）
        new_client: MongoClient | None = None
        try:
            new_client = MongoClient(
                host=_hub.dev.MONGO_HOST,
                port=_hub.dev.MONGO_PORT,
                # ✅ 本地工具：不预热连接（minPoolSize=0）；
                #    生产若多 worker，每个进程独立池——20 足够。
                maxPoolSize=20,
                minPoolSize=0,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
            )
            new_db = new_client[_hub.dev.MONGO_DB]
            new_db.command("ping")
            _create_indexes_on(new_db)

            _client = new_client
            _db = new_db
            logger.info(
                "[DB] ✅ MongoDB 连接成功 (%s:%d/%s)",
                _hub.dev.MONGO_HOST, _hub.dev.MONGO_PORT, _hub.dev.MONGO_DB,
            )
        except Exception as e:
            # ✅ 异常路径：显式关闭半成品 client，避免连接泄漏
            if new_client is not None:
                try:
                    new_client.close()
                except Exception:
                    pass
            logger.error("[DB] ❌ MongoDB 连接失败: %s", e)
            raise


def _create_indexes_on(db: Database) -> None:
    """同步建索引。"""
    db["tasks"].create_indexes([
        IndexModel([("task_id", ASCENDING)], unique=True),
        IndexModel([("created_at", DESCENDING)]),
        IndexModel([("status", ASCENDING)]),
    ])
    db["plans"].create_indexes([
        IndexModel([("plan_id", ASCENDING)], unique=True),
        IndexModel([("created_at", DESCENDING)]),
    ])


def get_db() -> Database:
    """
    同步获取 db 句柄。

    ⚠️ 签名从 async 改为同步——调用方（core/db/ 内部）必须去掉 await。
       但 core/db/ 的**业务函数签名仍保持 async**——那是另一回事。

    ⚠️ 失败冷却：DB 最近失败过时直接拒，不让每个请求都卡 5 秒超时。
    """
    global _last_fail_time

    if _client is None:
        if time.time() - _last_fail_time < _FAIL_COOLDOWN:
            raise RuntimeError("[DB] MongoDB 最近连接失败，冷却中")
        try:
            init_db()
        except Exception:
            _last_fail_time = time.time()
            raise
    if _db is None:
        raise RuntimeError("[DB] 数据库未初始化")
    return _db


def close_db() -> None:
    """
    显式关闭连接。

    ⚠️ 幂等：无连接时静默返回。
       GracefulShutdown（SIGTERM/SIGINT）和 atexit 同时注册——
       必须能安全重入。

    ⚠️ 关闭失败只记 warning，不抛——atexit 阶段抛异常会被 Python
       吞掉，不如自己记清楚。
    """
    global _client, _db
    with _init_lock:
        if _client is None:
            return
        try:
            _client.close()
            logger.info("[DB] MongoDB 连接已关闭")
        except Exception as e:
            logger.warning("[DB] 关闭连接失败: %s", e)
        _client = None
        _db = None


# ═══════════════════════════════════════════════════════════════════
#  atexit 兜底
# ═══════════════════════════════════════════════════════════════════
# GracefulShutdown.install() 在 debug 模式下会被 WERKZEUG_RUN_MAIN
# 守卫跳过——atexit 是那条路径的兜底。
# 挂 sys 抗 importlib.reload——与 workflow.py / llm/client.py 同策略。
if not getattr(sys, "_task_planner_db_atexit_registered", False):
    atexit.register(close_db)
    sys._task_planner_db_atexit_registered = True


__all__ = ["init_db", "get_db", "close_db"]
