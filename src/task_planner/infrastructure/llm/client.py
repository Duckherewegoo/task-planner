"""client.py — AsyncOpenAI 客户端单例 + 并发信号量

⚠️ 关于 loop 绑定：
   本文件里的 AsyncOpenAI / asyncio.Lock / asyncio.Semaphore 都绑定
   创建时的 event loop。Python 3.10+ 的这些对象在首次 await 时
   绑定 loop，跨 loop 复用抛 RuntimeError 或 "Event loop is closed"。

   生产"单 loop 常驻"没问题；但以下场景会出问题：
     - pytest-asyncio（每用例新 loop）
     - app.py 里 asyncio.run(init_db())（模块级创建+销毁 loop）
     - 任何"进程内创建多个 loop"的场景

   本文件用"按 loop 缓存"策略——检测到当前 loop 与缓存的 loop
   不同时，重新创建（旧对象让 GC 回收，无法显式关闭因为 loop 已死）。

⚠️ 关于 aclose：
   AsyncOpenAI 包装了 httpx.AsyncClient（自建 http_client）。
   连接池需要显式 aclose，否则进程退出时可能留 ResourceWarning。

   但**当前项目是 Dash（WSGI/Flask 底层）——没有 ASGI lifespan
   shutdown 钩子可以挂**。所以：
     - 本文件导出 aclose_llm_client() 供未来迁 ASGI 时使用
     - 当前用 atexit 尽力关闭（同步上下文，多半失败）
     - 迁移到 FastAPI/Starlette 后，在 lifespan.shutdown 里 await
       aclose_llm_client()

⚠️ 关于 app.py 的上游炸弹：
   app.py 的 _bootstrap_db() 在模块 import 时 asyncio.run(init_db())，
   创建 loop A 跑完即销毁。如果 init_db 内部创建了任何绑定 loop 的
   asyncio 对象，后续真实请求在新 loop 使用时会崩。
   本文件的"按 loop 缓存"策略能缓解 LLM 客户端这一侧——
   但 init_db 内部的 asyncio 对象仍需 app.py 侧修复（见跨文件待办）。
"""
import asyncio
import atexit
import sys

from openai import AsyncOpenAI

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  按 loop 缓存的状态
# ═══════════════════════════════════════════════════════════════════
# 每个 asyncio 对象都记录"值 + 绑定它的 loop"。
# 访问时检测 loop 变化——变了就重建。
_client: AsyncOpenAI | None = None
_client_loop: asyncio.AbstractEventLoop | None = None

_client_lock: asyncio.Lock | None = None
_client_lock_loop: asyncio.AbstractEventLoop | None = None

_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None

_dashscope_available = False


def _get_client_lock() -> asyncio.Lock:
    """获取绑定当前 loop 的 client lock（loop 变化时重建）。"""
    global _client_lock, _client_lock_loop
    loop = asyncio.get_running_loop()
    if _client_lock is None or _client_lock_loop is not loop:
        _client_lock = asyncio.Lock()
        _client_lock_loop = loop
    return _client_lock


def get_semaphore() -> asyncio.Semaphore:
    """
    获取绑定当前 loop 的并发信号量（loop 变化时重建）。
    由 llm/api.py 和 llm/core.py 共用。

    ⚠️ 语义：并发上限是"每 loop 独立"的——不同 loop 不共享同一
       Semaphore。生产单 loop 场景下等价于全局上限。
    """
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(_hub.dev.LLM_MAX_CONCURRENT)
        _semaphore_loop = loop
    return _semaphore


async def get_llm_client() -> AsyncOpenAI | None:
    """
    异步懒加载 AsyncOpenAI 客户端（协程安全 + loop 感知）。

    行为：
      - 首次调用时创建；同 loop 内后续调用直接返回缓存
      - 检测到 loop 变化时重建（旧实例不显式关闭——它的 loop 已死，
        无法 await aclose；连接池随 GC 回收，可能留 ResourceWarning）
      - USE_MOCK_LLM 或无 API key 时返回 None
    """
    global _client, _client_loop, _dashscope_available

    loop = asyncio.get_running_loop()

    # 快路径：同 loop 且已创建 → 直接返回
    if _client is not None and _client_loop is loop:
        return _client

    async with _get_client_lock():
        # double-check（锁内重新确认）
        if _client is not None and _client_loop is loop:
            return _client

        # loop 变了——旧 _client 无法显式关闭（loop 已死），
        # 覆盖前记一条 warning，让运维知道有连接池会泄漏。
        if _client is not None and _client_loop is not loop:
            logger.warning(
                "[LLMClient] 检测到 event loop 变化——旧 client 无法显式关闭，"
                "连接池将随 GC 回收（可能留 ResourceWarning）"
            )
            _client = None
            _client_loop = None

        if _hub.dev.USE_MOCK_LLM or not _hub.dev.DASHSCOPE_API_KEY:
            logger.info("[LLMClient] 启用 Mock 模式")
            return None

        try:
            base_url = (
                _hub.dev.DASHSCOPE_BASE_URL
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            # 函数内 import httpx——只在真实调用时才需要它，
            # 不在模块级强依赖（与 openai 的 import 时机对齐）。
            import httpx

            timeout = httpx.Timeout(_hub.dev.LLM_TIMEOUT, connect=5.0)
            http_client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=_hub.dev.LLM_MAX_CONCURRENT * 2
                ),
            )
            _client = AsyncOpenAI(
                api_key=_hub.dev.DASHSCOPE_API_KEY,
                base_url=base_url,
                http_client=http_client,
            )
            _client_loop = loop
            _dashscope_available = True
            logger.info(
                "[LLMClient] AsyncOpenAI 客户端就绪 (base=%s, max_conn=%d)",
                base_url,
                _hub.dev.LLM_MAX_CONCURRENT,
            )
        except ImportError:
            logger.error("[LLMClient] openai 未安装: pip install openai")
            return None

    return _client


async def aclose_llm_client() -> None:
    """
    显式关闭 LLM 客户端及其连接池。

    适用场景：
      - ASGI 应用（FastAPI/Starlette）的 lifespan.shutdown 里 await
      - 测试的 autouse fixture 里 await（避免跨 loop 泄漏）

    ⚠️ 当前项目是 Dash（WSGI/Flask 底层），没有 lifespan 钩子——
       本函数只能靠 atexit 兜底（且 atexit 无法 await，
       只能用"当前 loop 还活着就 asyncio.run"的尽力策略）。
       TODO: 迁 ASGI 后，在 lifespan.shutdown 挂本函数。
    """
    global _client, _client_loop, _dashscope_available
    if _client is None:
        return
    try:
        # AsyncOpenAI.close() 会 aclose 内部 http_client
        await _client.close()
        logger.info("[LLMClient] 客户端已关闭")
    except Exception as e:
        # 常见失败：loop 已死 → RuntimeError；连接池已关 → 幂等忽略
        logger.warning("[LLMClient] 关闭失败（可能 loop 已死）: %s", e)
    finally:
        _client = None
        _client_loop = None
        _dashscope_available = False


# ═══════════════════════════════════════════════════════════════════
#  atexit 兜底
# ═══════════════════════════════════════════════════════════════════
def _atexit_close() -> None:
    """
    进程退出时尽力关闭客户端。

    ⚠️ atexit 是同步上下文——无法 await。策略：
       开一个临时 loop 跑 aclose_llm_client()。
       - 若当前进程无活跃 loop → 能成功
       - 若原 loop 已死 → 会抛异常，只记 warning

    本函数是"尽力而为"，不是"可靠清理"。
    """
    if _client is None:
        return
    try:
        asyncio.run(aclose_llm_client())
    except Exception as e:
        logger.warning(
            "[LLMClient] atexit 关闭失败（loop 已死或不可用）: %s", e
        )


# 挂 sys 抗 importlib.reload——与 workflow.py 同策略
if not getattr(sys, "_task_planner_llm_atexit_registered", False):
    atexit.register(_atexit_close)
    sys._task_planner_llm_atexit_registered = True


__all__ = ["get_llm_client", "get_semaphore", "aclose_llm_client"]
