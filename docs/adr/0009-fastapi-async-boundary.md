---
id: 0009-fastapi-async-boundary
aliases: []
tags: [architecture, async, adr]
---

# ADR 0009: 使用 FastAPI Backend 统一异步边界

- **Status**: Accepted（2026-11-25 修订）
- **Date**: 2026-11-23（初稿）/ 2026-11-25（修订）
- **Deciders**: 项目维护者
- **Supersedes**: [ADR 0001 - 异步边界分层](./0001-async-boundary.md)
- **Superseded by**: —
- **Related**:
  - 决策链：ADR-0002（全链路异步）→ ADR-0001（分层妥协）→ **ADR-0009（本决策）**
  - 设计文档：`docs/ARCH_FUTURE.md` §一、`docs/DESIGN_task_context.md`（**v2**）
  - 落地代码：`src/task_planner/main/app.py`、`src/task_planner/core/db/client.py`、`src/task_planner/services/agent/session.py`、`src/task_planner/services/stream/state.py`、`src/task_planner/services/agent/commands.py`
  - 归档：`src/task_planner/infrastructure/abandon/async_runtime.py`
  - 依赖变更：`pyproject.toml`（motor 提升为主依赖、新增 uvicorn、dash[fastapi]）
  - commit：9d1d4fc

> **修订说明（2026-11-25）**：初稿的「迁移说明」写的是当时预期形态，落地后发现
> 与真实代码有 6 处出入（`server` 入口、lifespan 写法、Motor 初始化时机、
> `close()` 是否 await、cleaner 迁移形态、`attach_runner` 归属）。本版以
> **实际代码为准**重写这些条目，并在末尾保留「初稿与落地的差异」对照表供追溯。
> ADR 正文（Context / Decision / Alternatives / Consequences）不变。

---

## Context

### 历史演变

**ADR-0002（2026-09-28）** 曾决定全链路异步：Motor + AsyncOpenAI + `asyncio.create_task`。
但在 Dash 4.4.1 WSGI backend（`asgiref.async_to_sync`）下实测失败：

- 同一 thread 连续回调，`id(asyncio.get_running_loop())` 序列 = **A → B → C**
- Motor 绑定 loop 后跨回调复用抛 `Event loop is closed`
- `create_task` 的后台任务在回调返回后立即被取消
- `asyncio.Lock` / `Semaphore` / `httpx.AsyncClient` 全部失效

**ADR-0001（2026-10-13/14）** 作为补救引入**异步边界分层**：

| 层 | 选型 | 理由 |
|---|---|---|
| UI 回调 | `async def` 声明，内部同步 | 兼容 WSGI |
| 后台任务 | `async_runtime` 常驻 loop（独立 daemon 线程） | 跨回调存活 |
| DB 客户端 | 同步 `pymongo` | 不绑 loop，永久复用 |
| LLM 客户端 | `AsyncOpenAI` | 真异步长耗时 IO |

ADR-0001 同时保留 DB 业务函数的 `async def` 签名（内部同步调用，即「假 async」），
使调用方 `await` 零改动，为未来切回异步留口子。其**方案 C**（立即切 ASGI backend）
被标记为「暂缓，非否决」，切换路径写入 `ARCH_FUTURE.md` §一。

### 触发原因（2026-11-11）

0001 生效约一个月后，以下条件逐渐明确：

1. **`async_runtime` 是进程级单例**，多 worker 时每 worker 各有一个 loop，跨进程任务状态共享无解——正是 0001 自己列出的重审条件。
2. **Dash 4.2+ 正式提供 `backend="fastapi"`**，由 uvicorn 提供持久 event loop，回调本身为 async，`create_task` 即可跨回调存活。方案 C 前提成熟。
3. **`pymongo` 同步驱动在 async 架构中是异物**——虽不绑 loop，但阻塞 I/O 进入 uvicorn 的 event loop 线程会抵消 ASGI 收益，「假 async」签名长期存在认知负担。
4. **测试规模足以支撑架构迁移的回归验证**。本地实测：231 单元测试 + 44 集成测试（43 MongoDB + 1 Redis）；CI 数字以当次 Actions 输出为准，本文不钉死总数。

### 约束

- 迁移不破坏既有测试
- MongoDB 连接必须稳定跨回调复用
- LLM 流式调用仍需 `cancel_event` 打断能力
- 部署从 `app.run()` / Werkzeug 切换为 uvicorn，对外仍暴露 HTTP 服务

---

## Decision

执行 ADR-0001 方案 C：**Dash FastAPI backend + uvicorn 持久 loop + Motor 异步驱动** 统一异步边界。

1. **Dash backend 切换**：`Dash(name, backend="fastapi")`。
2. **启动方式**：`app.run()` / Werkzeug → `uvicorn.run(...)`，Dash 回调直接跑在 uvicorn 持久 loop 上。
3. **后台任务**：不再 `async_runtime.submit(...)`，改为 `asyncio.create_task(...)`，天然跨回调存活。
4. **数据库驱动**：同步 `pymongo` → 异步 **Motor ≥ 3.6.0**。`AsyncIOMotorClient` 在首次 `await` 时绑定 uvicorn loop，之后跨回调复用。
5. **`async_runtime` 废弃**：`infrastructure/async_runtime.py` 移至 `abandon/`，职责由 uvicorn event loop 原生承担。
6. **依赖调整**：`motor>=3.6.0` 提升为主依赖；`pymongo>=4.5.0` 保留以控版本下限；`db_extra = []`（保留组名向后兼容）；新增 `uvicorn[standard]>=0.30.0`；`dash[async]` → `dash[async,fastapi]>=4.4.0`。
7. **签名兼容**：ADR-0001 保留的 `async def` DB 函数签名不变，函数体由「同步 pymongo」改为「await Motor」——调用方零改动，兑现 0001 预设的迁移路径。

---

## Alternatives Considered

### A. 保持 ADR-0001 现状（WSGI + async_runtime + pymongo）

**未采用。** 可用，但：异步边界分裂为「UI 一次性 loop + 后台常驻 loop」，心智负担高；`pymongo` 同步调用在 async 架构中是异物；多 worker 下 `async_runtime` 单例模型无法跨进程共享；0001 自身的重审条件已被触及。

### B. 继续 async_runtime，数据库换 Motor

**未采用。** Motor 绑定 loop；WSGI 一次性 loop 模型下跨回调复用仍不可靠。

### C. FastAPI backend + Motor（选中）

uvicorn 持久 loop 解决跨回调存活；Motor 绑定后可稳定复用；移除 `async_runtime` 简化架构；与 0001 预设路径一致。

### D. 全同步（连 LLM 也用同步 openai）

**未采用。** LLM 调用最长 60s，同步阻塞会卡住 uvicorn worker；`cancel_event` 打断能力丧失。

---

## Consequences

### Positive

- **异步边界统一**：UI 回调、后台任务、LLM 调用、DB 访问共享同一 event loop
- **`asyncio.create_task` 原生可用**：无需独立 daemon 线程，任务跨回调存活
- **Motor 连接池复用**：绑定 uvicorn loop 后跨回调稳定
- **移除 `async_runtime`**：减少约 100 行维护代码与独立线程开销
- **ADR-0001 的「假 async」完成兑现**：DB 函数体改为真实 `await`，签名不变，调用方零改动
- **部署模型现代化**：uvicorn 支持 `--workers N`、graceful shutdown、lifespan/事件钩子

### Negative（接受的代价）

- **Motor 初始化时机敏感**：必须在 uvicorn loop 内首次 `await` 时绑定。**落地形态：由 `get_db()` 懒初始化（`init_db` 内 `await db.command("ping")` 即绑定点），而非在 startup 钩子显式调用**——懒初始化天然满足。在回调外提前实例化会绑错 loop。
- **`uvicorn --reload` 开发模式下 loop 反复重建**：Motor 客户端需随 loop 重建，当前由懒初始化缓解；若出现 `Task attached to a different loop`，需检查初始化路径。
- **回调中禁止 CPU 密集操作**：单 loop 下阻塞会卡住所有请求；`_executor.py` 的耗时逻辑须 I/O 密集或走 `run_in_executor`。
- **多 worker 部署需重新评估共享状态**：`TaskStreamState` 在内存中，`--workers N > 1` 时跨 worker 不可见；需 Redis session store 或 sticky session。
- **【新增】无显式同步阻塞检测，只有 loop 绑定检测兜底**：本决策不提供"同步调用进入回调路径"的探针，`debugpy` / `lxml` 这类阻塞库一旦进回调，表现仍是轮询抖动而非报错。实际落地的是 **loop 绑定检测**——`_get_sessions_lock()`（`services/agent/session.py`）与 `_get_resume_lock()`（`services/agent/commands.py`，以 `(lock, loop)` 元组存于 `_resume_locks`）在首次 `await` 时把锁绑到当前 loop，检测到 `get_running_loop()` 变化即重建锁，解决 pytest-asyncio 每用例新 loop 下的 `is bound to a different event loop`。它防的是跨 loop 复用，不是阻塞。
- **【新增】`asyncio.Lock` 是 loop-local 的，与 §3.2 的 task 同构**：模块级锁在多 loop 场景下必须惰性创建 + 换 loop 重建，这是 ASGI 迁移后唯一需要额外维护的并发基建。
- **【新增】事件钩子写法受 FastAPI 版本约束**：`FastAPI` 实例上**没有** `add_event_handler`（它在内部 Starlette Router 上），`on_event` 与 `add_event_handler` 均已 deprecated。落地采用 `app.server.router.add_event_handler(...)`，pyright strict 下无需 `cast` 掩盖类型。迁移到 `asynccontextmanager` lifespan 的收益有限（会与 Dash 自身 lifespan 竞争），暂不做。

### Neutral

- **【修订】启动入口**：`main()` 内 `uvicorn.run(app.server, host, port, reload=debug, ...)`；console script 为 `task-planner = task_planner.main.app:main`。**不存在模块级 `server` 变量**，`uvicorn main:server` 的写法不可用。
- **【修订】关闭顺序**：清理循环 → `state_cleaner.stop()` → `aclose_llm_client()` → `close_db()`。DB 必须最后关，因为前两步仍会碰 DB。每步独立兜底，单步失败不阻断后续。
- **【修订】Motor 关闭**：`AsyncIOMotorClient.close()` 是**同步**方法，不 `await`；`close_db()` 幂等，另有 `atexit` 同步兜底。
- `abandon/async_runtime.py` 保留归档，可作回退参考基线。

---

## Revocation

当以下任一情况发生，且**无法通过局部重构、配置调优或 `run_in_executor` 剥离**解决时，应正式撤销本决策，回退 ADR-0001 或引入分布式任务队列（Celery / Redis Streams / RabbitMQ）：

### 1. uvicorn 单 Event Loop 被阻塞，影响交互实时性

**撤销信号**：`_executor.py` 节点逻辑或自定义工具中不可避免引入 CPU 密集操作（复杂 DAG 拓扑排序、大批量序列化、同步阻塞 SDK），且无法用 `run_in_executor` 剥离，导致前端轮询（Interval 1000ms）超时、流式推送卡顿、HITL 确认延迟恶化。

### 2. Motor 生命周期与 uvicorn 进程模型冲突

**撤销信号**（须无法根治）：

- `--reload` 导致 loop 反复重建，`Task attached to a different loop` 频繁，懒初始化无法规避
- 多 worker 下 `TaskStreamState` 或后台 `asyncio.Task` 意外跨 worker 共享，任务状态错乱
- Graceful shutdown 时未完成 Motor 操作因 loop 提前关闭而静默丢失，事件钩子无法兜底

### 3. 跨进程 / 跨机器任务调度需求出现

**撤销信号**：需要把 DAG 节点分发到不同机器/容器；需要进程重启后由其他进程接管未完成任务（MongoDB 有状态，但执行上下文强依赖内存 loop）；需要横向扩展 UI 实例而不引入 IPC 或消息队列。

> 本节与 `DESIGN_task_context.md` v2 §七的触发条件**同源**——那边只做引用，不另立清单。

### 4. Dash FastAPI Backend 被废弃或生态不兼容

**撤销信号**：Dash 官方废弃 `backend="fastapi"` 或 `dash[fastapi]`；或必须集成深度依赖 WSGI 中间件的企业级组件（认证网关、APM 探针）且无法在 ASGI 层适配。此时回退 WSGI 并重新启用 `async_runtime`。

### 5. 异步依赖生态断裂

**撤销信号**：核心依赖（LangGraph、OpenAI SDK）放弃异步接口；或关键组件仅提供同步阻塞 SDK 且包装成本高于收益；或 Motor 出现无法修复且影响核心路径的 bug，而 pymongo 已能满足性能需求（此时回退 ADR-0001 同步方案）。

### 撤销后的动作

1. 本 ADR 状态改为 `Deprecated` 或 `Superseded by ADR-00XX`
2. 新建 ADR 记录回退方案或新架构
3. 将 `abandon/async_runtime.py` 恢复为正式代码
4. 更新 `ARCH_FUTURE.md` 触发条件章节与 `DESIGN_task_context.md` v2 的 §七

---

## 迁移说明（Migration Note，非 ADR 正文）

> 真正的改造步骤与回归清单应放 PR 描述；本节只记**落地后的真实形态**，供后人对照。

### 已落地的代码改造

| # | 文件 | 落地形态 |
|---|---|---|
| 1 | `main/app.py` | `Dash(__name__, backend="fastapi", ...)`；`boost(app, backend="fastapi", app_kind="dash", ...)` 在 `register_config_callbacks` **之前**；`main()` 内 `uvicorn.run(cast/app.server, ...)`；钩子注册**幂等**（模块级标志位，避免 reload 重复注册）；`_on_startup` 起 300s 清理循环，`_on_shutdown` 按序兜底关闭 |
| 2 | `core/db/client.py` | `AsyncIOMotorClient`；`init_db()` 内 `await db.command("ping")` 为 loop 绑定点；`get_db()` 懒初始化 + 10s 失败冷却；`close_db()` 先置 `None` 再 `close()`，**不加锁**（关停阶段 `Lock.acquire()` 可能抛）；`atexit` 同步兜底；模块级 `asyncio.Lock` 懒绑定 loop |
| 3 | `services/stream/launcher.py` | `async_runtime.submit` → `asyncio.create_task`，句柄交 `TaskStreamState.set_task` |
| 4 | `services/stream/cleaner.py` | **不是**改成 `create_task`：改为由 lifespan 每 300s 调 `cleanup_stale_sessions()`；`state_cleaner.stop()` 在 shutdown 第一步 |
| 5 | `services/agent/session.py` | 后台 task 句柄**不在** `TaskSession` 上；`_task` 属 `TaskStreamState`（`abort_worker` 用）。`TaskSession` 只留 `thread_id` / `cancel_event` / `start_time` + `config` property；`cleanup_stale_sessions()` 由 300s 循环驱动，泄漏已堵 |
| 6 | `services/agent/commands.py` | **已完成 —— 双通道取消**：`_aborts: dict[str, Callable]` + `register_abort()` / `unregister_abort()`；`launcher._run_worker` 在 `create_task` 后注册 `state.abort_worker`，`finally` 里注销。`cancel_task` 先 `_aborts.pop(thread_id)()` 硬取消，再设 `cancel_event` 做协作式取消。组合层注入，commands 未反向依赖 stream 层（设计见 `DESIGN_task_context.md` v2 §4.1） |

### 依赖变更（`pyproject.toml`）

```toml
[project]
dependencies = [
    "motor>=3.6.0",
    "pymongo>=4.5.0",           # motor 底层依赖，显式声明以控版本下限
    "dash[async,fastapi]>=4.4.0",
    "uvicorn[standard]>=0.30.0",
    "lxml>=6.0.2",              # 3.14 预编译轮子自 6.0.1 起才有，5.x 需现场编译
    # ... 其余不变
]

[project.optional-dependencies]
db_extra = []                   # 清空，保留组名向后兼容
# cairosvg 建议移入 extra（Windows 无 GTK 运行时装不上），导出侧优雅降级
```

### 回归验证

```bash
pytest tests/ -v                                    # 全量
pytest tests/test_package/test_mongodb.py -v -m integration   # Motor 跨回调复用
grep -rn "async_runtime" src/ tests/ | grep -v abandon | grep -v test   # 期望无输出
task-planner                                        # 启动冒烟
# 手动：触发一次完整 DAG，确认后台任务在回调返回后继续运行；点取消观察实际停止延迟
```

### ADR-0001 / ADR-0002 状态变更

- `0001-async-boundary.md` 元信息追加：`Status: Superseded by ADR 0009` + `Superseded Date: 2026-11-23`
- `0002-full-chain-async.md` 元信息追加：`Status: Superseded by ADR 0009`（其全链路异步意图由 0009 兑现，中间路径作废）

---

## 初稿与落地的差异（2026-11-25 勘误）

| # | 初稿写法 | 落地真实形态 |
|---|---|---|
| 1 | 暴露 `server = app.server`，`uvicorn main:server` | `main()` 内 `uvicorn.run(app.server, ...)`，入口是 `task-planner` 脚本 |
| 2 | lifespan 内 `init_db()` / `await client.close()` | `get_db()` 懒初始化；`close_db()` 同步 `close()`，shutdown 调用 |
| 3 | `cleaner.py` 改 `asyncio.create_task` | 改为 lifespan 每 300s 调 `cleanup_stale_sessions()` |
| 4 | `session.attach_runner(task)` 历史坑 | 句柄在 `TaskStreamState._task`，`TaskSession` 无此字段 |
| 5 | 用 `@app.server.on_event` 装饰器 | `app.server.router.add_event_handler(...)`（实例上无该方法；装饰器形式已 deprecated） |
| 6 | 未记录 | `_SESSION_CLEANUP_INTERVAL=300` 硬编码兜底，应挪入 `hub.dev.SESSION_CLEANUP_INTERVAL`（与 `_ttl` 同支持热重载） |
