# good_addons v7 — 企业级 Python 运行时增强层

## 结构（粗粒度，各司其职）
- `good.py`      对外接口：`boost()` / `diag()` / 装饰器与组件再导出
- `core.py`      所有与框架无关的通用能力（日志、脱敏、装饰器、EventBus、监控、安全头…）
- `registry.py`  扫描 `support/*_support.py`，文件名即 backend 名
- `support/*.py` 每个文件只负责一个框架/服务器如何接上 core

## 用法
    from good_addons import boost
    boost(app, backend="fastapi", server="uvicorn")       # 显式
    boost(app, backend="flask")                            # 隐式服务器
    boost(dash_app)                                        # Dash 自动识别，底层按 Flask 增强

## Header 约定（严格区分大小写）
- 请求：X-Trace-ID / X-Request-ID / X-Correlation-ID、X-User-ID、X-Session-ID
- 响应：X-Request-ID、X-Response-Time、X-Response-Time-Ms
Header 名按规范写法精确匹配，X-Trace-ID 与 X-Trace-Id 视为两个不同的头。

## 测试
    python3 -m pytest good_addons/tests -q
