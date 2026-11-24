# ╔══════════════════════════════════════════════════════════════════════╗
# ║  good_addons.support — 框架 / 服务器适配器包                       ║
# ║  License: GPLv3                                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
good_addons.support —— 每个 ``<name>_support.py`` 负责"如何给一个具体
框架或服务器打增强补丁"。
═══════════════════════════════════════════════════════════════════════

┌─ 适配器契约（新增 support 必须遵守）───────────────────────────────┐
│                                                                    │
│  1. 文件命名：``<name>_support.py`` → 自动注册为名字 ``<name>``。   │
│                                                                    │
│  2. 必须暴露模块级函数：                                            │
│         def enhance(app: Any, **kwargs: Any) -> dict[str, Any]:    │
│     返回 ``{"hooks": [...], ...}`` 描述本次装了哪些增强，           │
│     供 boost() 汇总进报告。                                         │
│                                                                    │
│  3. 顶层只 import 标准库 + ``good_addons.core``。                   │
│     **框架 import（flask / fastapi / quart / uvicorn ...）必须      │
│     延迟到 enhance() 内部**，保证 registry 扫描本包时零副作用、     │
│     缺依赖的适配器可被安全跳过。                                     │
│                                                                    │
│  4. 增强必须"尽力而为"：任何一步失败都应记日志而非向上抛，          │
│     绝不能因为增强失败而让用户的 app 起不来。                        │
│                                                                    │
│  5. 只用 core 暴露的稳定接口（单例 / RequestContext / Security /    │
│     ExceptionFingerprinter / new_trace_id / log / _ctx_*），        │
│     不反向 import good.py，避免循环依赖。                            │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

当前内置适配器：

    flask_support.py      → "flask"      WSGI 框架（含 Dash 默认底层）
    fastapi_support.py    → "fastapi"    ASGI 框架（Starlette 中间件）
    quart_support.py      → "quart"      ASGI 框架（Flask API 风格）
    uvicorn_support.py    → "uvicorn"    ASGI 服务器（ lifespan + 访问日志）
    hypercorn_support.py  → "hypercorn"  ASGI/WSGI 服务器（多worker 提示）

注意：Dash 不属于本层——它是"应用层形态"而非 HTTP 框架/服务器，
其专属增强（回调包装、_dash-component-suites 例外、trace 注入）由
``good.py`` 的 ``app_kind="dash"`` 钩子处理，与底层 backend 正交叠加。
"""

# 本文件刻意为空实现：契约写在 docstring，不 re-export 任何适配器，
# 以免 import 本包时连带触发子模块导入（保持扫描零副作用）。
