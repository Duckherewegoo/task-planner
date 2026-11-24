# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.registry — 极简 support 注册器                        ║
# ║  "文件名即名字"：目录即注册表，重名在文件系统层被物理消除          ║
# ║  License: GPLv3                                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
good_addons.registry —— 只回答一个问题："support/ 里有哪些适配器、叫什么"。
═══════════════════════════════════════════════════════════════════════

设计要点（为何这层能薄到 ~40 行）：

  1. **命名即契约**：``support/<name>_support.py`` 自动注册为 ``"<name>"``。
     ``flask_support.py → "flask"``、``uvicorn_support.py → "uvicorn"``。
     想支持新框架？丢一个 ``xxx_support.py`` 进目录即可，无需改本文件。

  2. **重名天然不可能**：同一目录下不能存在两个 ``flask_support.py``，
     操作系统替我们兜底，因此这里**不需要任何重名检测代码**。

  3. **别名靠薄包装，不靠 SUPPORTS 常量**：若将来真需要 ``fastapi2``
     这类别名，写一个 re-export 模块 ``fastapi2_support.py`` 即可：:

         from good_addons.support.fastapi_support import enhance  # noqa: F401

  4. **扫描零副作用**：本模块只 importlib 模块对象，不触发框架 import——
     各 support 必须把 ``import fastapi`` 等延迟到 ``enhance()`` 内部。
     单个 support 导入失败（例如缺依赖）只跳过它，不影响其余适配器。

对外只暴露两个函数：``discover()``（全量扫描）与 ``resolve()``（按名取用）。
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import NamedTuple


class SupportAdapter(NamedTuple):
    """一个已解析的适配器：名字 + 模块对象。"""

    name: str
    module: ModuleType


def discover() -> dict[str, ModuleType]:
    """扫描 ``good_addons/support/``，返回 ``{name: module}``。

    仅纳入以 ``_support`` 结尾、且暴露可调用 ``enhance`` 的模块；
    导入失败或缺 ``enhance`` 的模块被静默跳过（保证扫描永不抛异常）。
    """
    from . import support as pkg

    pool: dict[str, ModuleType] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.name.endswith("_support"):
            continue
        try:
            mod = importlib.import_module(f"good_addons.support.{info.name}")
        except Exception:
            continue
        if not callable(getattr(mod, "enhance", None)):
            continue
        pool[info.name[: -len("_support")]] = mod  # fastapi_support → "fastapi"
    return pool


def resolve(backend: str | None, server: str | None) -> list[SupportAdapter]:
    """按名字解析 backend / server 适配器，保持 [backend, server] 顺序。

    Args:
        backend: 处理 HTTP 的 Web 框架名（flask / fastapi / quart）。
        server:  跑 ASGI/WSGI 的服务器名（uvicorn / hypercorn）。

    Returns:
        有序的适配器列表（去重：backend == server 时只保留一个）。

    Raises:
        ValueError: 名字未找到，或其模块缺少 ``enhance()``。
    """
    pool = discover()
    chosen: list[SupportAdapter] = []
    seen: set[str] = set()
    for name in (backend, server):
        if not name or name in seen:
            continue
        mod = pool.get(name)
        if mod is None:
            raise ValueError(f"未找到 support: {name!r}（可用: {sorted(pool)}）")
        chosen.append(SupportAdapter(name, mod))
        seen.add(name)
    return chosen
