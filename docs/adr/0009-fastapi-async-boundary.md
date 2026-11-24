---
id: 0009-fastapi-async-boundary
aliases: []
tags: []
---

# ADR 0009: 使用 FastAPI Backend 统一异步边界

- **Status**: Accepted
- **Date**: 2026-11-23
- **Deciders**: 项目维护者
- **Supersedes**: [ADR 0001 - 异步边界分层](./0001-async-boundary.md)
- **Related**:
  - 被取代的决策链：ADR-0002（全链路异步）→ ADR-0001（分层妥协）→ ADR-0009（本决策）
  - 设计文档：`docs/ARCH_FUTURE.md` §一「触发条件：什么时候切方案 C」
  - 落地代码：`src/task_planner/main/app.py`、`src/task_planner/core/db/client.py`、`src/task_planner/infrastructure/abandon/async_runtime.py`
  - 依赖变更：`pyproject.toml`（motor 提升为主依赖、新增 uvicorn、dash[fastapi]）
  - commit：9d1d4fc

## Context

### 历史演变

**ADR-0002（2026-09-28）** 曾决定全链路异步：Motor + AsyncOpenAI + `asyncio.create_task`。
但在 Dash 4.4.1 WSGI backend（`asgiref.async_to_sync`）下实测失败：

- 同一 thread 连续回调，`id(asyncio.get_running_loop())` 序列 = **A → B → C**
- Motor 绑定 loop 后跨回调复用抛 `Event loop is closed`
- `create_task` 的后台任务在回调返回后立即被取消
- `asyncio.Lock` / `Semaphore` / `httpx.AsyncClient` 全部失效

**ADR-0001（2026-10-13/14）** 作为补救措施，引入**异步边界分层**：

| 层 | 选型 | 理由 |
|---|---|---|
| UI 回调 | `async def` 声明，内部同步 | 兼容 WSGI |
| 后台任务 | `async_runtime` 常驻 loop（独立 daemon 线程） | 跨回调存活 |
| DB 客户端 | 同步 `pymongo` | 不绑 loop，永久复用 |
| LLM 客户端 | `AsyncOpenAI` | 真异步长耗时 IO |

ADR-0001 同时保留了 DB 业务函数的 `async def` 签名（内部同步调用，即"假 async"），
使调用方 `await` 零改动，为未来切回异步留口子。

ADR-0001 的**方案 C**（立即切 ASGI backend）被标记为"暂缓，非否决"，
切换路径写入 `ARCH_FUTURE.md` §一，触发条件包括：多 worker 部署需求、单进程撑不住并发等。

### 当前触发原因（2026-11-11）

以下条件在 0001 生效后约一个月逐渐明确：

1. **`async_runtime` 是进程级单例**，多 worker 部署时每个 worker 各有一个 loop，
   跨进程任务状态共享无解——这正是 0001 自己列出的重审条件之一。
2. **Dash 4.2+ 正式提供 `backend="fastapi"`**，由 uvicorn 提供持久 event loop，
   回调本身为 async，直接 `create_task` 即可跨回调存活。0001 方案 C 的前提条件已成熟。
3. **`pymongo` 同步驱动在 async 架构中是异物**——虽然不绑 loop，但阻塞 I/O 进入
   uvicorn 的 event loop 线程会抵消 ASGI 收益，且"假 async"签名长期存在认知负担。
4. **项目测试规模增长到 231 passed**，回归验证能力足以支撑架构迁移。

### 约束

- 迁移不能破坏现有 231 个测试
- MongoDB 连接必须稳定跨回调复用
- LLM 流式调用仍需 `cancel_event` 打断能力
- 部署方式从 `app.run()` / Werkzeug 切换为 uvicorn，但对外仍暴露 HTTP 服务

## Decision

执行 ADR-0001 方案 C，采用 **Dash FastAPI backend + uvicorn 持久 loop + Motor 异步驱动** 统一异步边界：

1. **Dash backend 切换**：

   ```python
   Dash(name, backend="fastapi")
   ```

2. **启动方式**：从 `app.run()` / Werkzeug 改为 `uvicorn` 启动，
   Dash 回调直接运行在 uvicorn 的持久 event loop 上。

3. **后台任务**：不再提交到 `infrastructure/async_runtime.submit()`，
   改为直接 `asyncio.create_task(...)`，任务天然跨回调存活。

4. **数据库驱动**：从同步 `pymongo` 切换为异步 **Motor ≥ 3.6.0**。
   Motor 的 `AsyncIOMotorClient` 在首次 `await` 时绑定到 uvicorn 的 loop，
   之后跨回调复用，连接池正常。

5. **`async_runtime` 废弃**：`infrastructure/async_runtime.py` 移至 `abandon/`，
   不再参与运行。其职责由 uvicorn 的 event loop 原生承担。

6. **依赖调整**（`pyproject.toml`）：

   - `motor>=3.6.0` 提升为主依赖（原在 `db_extra` 组）
   - `pymongo>=4.5.0` 保留为 Motor 底层依赖, 保证版本下限可控，
   - `db_extra = []`（清空，保留组名仅向后兼容）
   - 新增 `uvicorn[standard]>=0.30.0`
   - `dash[async]` 改为 `dash[async,fastapi]>=4.4.0`

7. **签名兼容**：ADR-0001 保留的 `async def` DB 业务函数签名不变，
   但函数体从"同步调用 pymongo"改为"await Motor 异步调用"——调用方零改动，
   实现了 0001 预设的"未来切回异步只改函数体"路径。

## Alternatives Considered

### A. 保持 ADR-0001 现状（WSGI + async_runtime + pymongo）

**未采用。** 该方案已验证可用，但：

- 异步边界分裂为 UI 一次性 loop + 后台常驻 loop，心智负担高
- `pymongo` 同步调用在 async 架构中是异物
- 多 worker 部署时 `async_runtime` 单例模型无法跨进程共享
- 0001 自身的"触发重审条件"已开始被触及

### B. 继续使用 async_runtime，数据库换 Motor

**未采用。** Motor 绑定 event loop；WSGI 的一次性 loop 模型下跨回调复用仍不可靠。

### C. FastAPI backend + Motor（选中，即 ADR-0001 方案 C 的执行）

- uvicorn 持久 loop 解决跨回调存活问题
- Motor 绑定 loop 后可稳定复用
- 移除 `async_runtime` 简化架构
- 与 0001 预设的迁移路径完全一致

### D. 全同步（连 LLM 也用同步 openai）

**未采用。** LLM 调用最长 60s，同步阻塞会卡住 uvicorn worker；
`cancel_event` 打断能力丧失。

## Consequences

### Positive

- **异步边界统一**：UI 回调、后台任务、LLM 调用、DB 访问共享同一 event loop
- **`asyncio.create_task` 原生可用**：无需独立 daemon 线程，任务跨回调存活
- **Motor 连接池复用**：绑定 uvicorn loop 后跨回调稳定
- **移除 `async_runtime`**：减少 ~100 行维护代码和独立线程开销
- **ADR-0001 的"假 async"完成兑现**：DB 函数体改为真实 `await`，签名不变，调用方零改动
- **部署模型现代化**：uvicorn 支持 `--workers N`、graceful shutdown、lifespan 事件

### Negative（接受的代价）

- **Motor 初始化时机敏感**：必须在 uvicorn event loop 内首次 `await` 时绑定，
  懒初始化（`get_db` 首次触发）是正确做法；在回调外提前实例化会绑错 loop。
- **`uvicorn --reload` 开发模式下 loop 反复重建**：Motor 客户端需随 loop 重建，
  当前通过懒初始化缓解；若 reload 导致 `Task attached to a different loop` 需检查初始化路径。
- **回调中禁止 CPU 密集操作**：uvicorn 单 loop 下阻塞操作会卡住所有请求，
  节点执行器（`_executor.py`）中的耗时逻辑需确保是 I/O 密集或走 `run_in_executor`。
- **多 worker 部署需重新评估共享状态**：`TaskStreamState` 当前在内存中，
  `--workers N > 1` 时跨 worker 不可见；需引入 Redis session store 或 sticky session。

### Neutral

- 测试规模更新为 **231 passed, 44 deselected**
- `abandon/async_runtime.py` 保留归档，可作回退参考基线
- 启动命令从 `task-planner`（Werkzeug）变为 `uvicorn task_planner.main.app:server`

## Revocation

当以下任一情况发生，且**无法通过局部重构、配置调优或 `run_in_executor` 剥离**解决时，应正式撤销本决策，并考虑回退到 ADR-0001（WSGI + `async_runtime` + `pymongo`）或引入分布式任务队列（Celery / Redis Streams / RabbitMQ）：

### 1. uvicorn 单 Event Loop 被阻塞，影响交互实时性

当前架构假设所有耗时操作都是 I/O 密集型（LLM 调用、MongoDB 读写），可交由 `asyncio` 调度。

**撤销信号**：Dash 回调、`_executor.py` 中的节点逻辑或自定义工具中，不可避免地引入了 CPU 密集型操作（如复杂 DAG 拓扑排序、大批量数据序列化、同步阻塞 SDK 调用），且无法用 `asyncio.loop.run_in_executor` 剥离，导致：

- 前端轮询（`Interval 1000ms`）超时或无响应
- LangGraph 流式推送（`stream_state`）卡顿
- HITL 人工确认节点延迟，用户体验恶化

### 2. Motor 生命周期与 uvicorn 进程模型冲突

Motor 的 `AsyncIOMotorClient` 在首次 `await` 时绑定到当前 event loop。

**撤销信号**：出现以下任一现象且无法根治：

- 开发环境 `uvicorn --reload` 导致 loop 反复重建，`Task attached to a different loop` 错误频繁，懒初始化无法完全规避
- 生产环境使用多 worker（`uvicorn --workers N > 1`），而 `TaskStreamState` 或后台 `asyncio.Task` 意外跨 worker 共享，导致任务状态错乱
- Graceful shutdown 时，未完成的 Motor 操作因 loop 提前关闭而静默丢失，且 `lifespan` 钩子无法完全兜底

### 3. 跨进程 / 跨机器任务调度需求出现

本决策依赖"单进程内 uvicorn event loop + 内存 `TaskStreamState`"模型。

**撤销信号**：业务需要以下能力，而单 loop 架构无法低成本支持：

- 将 DAG 节点分发到不同机器或容器执行（当前节点执行在提交任务的同一 loop 中）
- 应用重启后，由其他进程接管未完成的任务（MongoDB 持久化了状态，但执行上下文强依赖内存 loop）
- 横向扩展 UI 实例，同时不引入复杂的进程间通信或外部消息队列

### 4. Dash FastAPI Backend 被废弃或生态不兼容

**撤销信号**：

- Dash 官方宣布废弃 `backend="fastapi"` 或 `dash[fastapi]` 包
- 项目必须集成深度依赖 WSGI 中间件的企业级组件（如特定认证网关、APM 探针），且无法在 ASGI 层适配
- 此时需回退到 WSGI 模型，并重新启用 `async_runtime`（ADR-0001）

### 5. 异步依赖生态断裂

**撤销信号**：

- 核心依赖（LangGraph、OpenAI SDK）放弃异步接口，或项目需引入关键组件但仅提供同步阻塞 SDK，且包装成本高于收益
- Motor 出现无法修复且影响核心路径的 bug，且 pymongo 同步驱动已能满足性能需求（此时应回退到 ADR-0001 的同步方案）

### 撤销后的预期动作

若触发上述条件，应：

1. 将本 ADR 状态改为 `Deprecated` 或 `Superseded by ADR-00XX`
2. 新建 ADR 记录回退方案或新架构（如引入 Celery、切回 WSGI + `async_runtime`）
3. 将 `abandon/async_runtime.py` 从归档恢复为正式代码
4. 更新 `ARCH_FUTURE.md` 中的触发条件章节，记录本次回退的决策链

---

> 以下为迁移执行说明，不属于 ADR 正文内容。真正的代码改造步骤与回归测试清单应放在 PR 描述中；本节仅为落地备忘。

## 迁移说明（Migration Note，非 ADR 内容）

### 代码改造清单

1. **`src/task_planner/main/app.py`**
   - `Dash(__name__, ...)` → `Dash(__name__, backend="fastapi")`
   - 移除 `app.run()`，改为暴露 `server = app.server` 供 uvicorn 导入
   - 启动时通过 lifespan 初始化 Motor 客户端，关闭时 `await client.close()`

2. **`src/task_planner/core/db/client.py`**
   - `pymongo.MongoClient` → `motor.motor_asyncio.AsyncIOMotorClient`
   - 所有 CRUD 函数体从 `collection.find(...)` 改为 `await collection.find(...).to_list()`
   - 保留 `async def` 签名（与 ADR-0001 兼容），函数体由假 async 变为真 async

3. **`src/task_planner/services/stream/launcher.py` & `cleaner.py`**
   - `async_runtime.submit(task)` → `asyncio.create_task(task)`
   - 移除对 `infrastructure.async_runtime` 的全部 import

4. **`src/task_planner/services/agent/stream.py`**
   - 校验 `session.attach_runner(task)` 中 `task` 变量作用域（历史上曾引用未定义变量，已在 0001 阶段删除该行）

### 依赖变更（`pyproject.toml`）

```toml
[project]
dependencies = [
    "motor>=3.6.0",
    "pymongo>=4.5.0",          # 由 motor 间接引入，可省略显式声明
    "dash[async,fastapi]>=4.4.0",
    "uvicorn[standard]>=0.30.0",
    # ... 其余不变
]

[project.optional-dependencies]
db_extra = []                  # 清空，保留组名向后兼容
dev = ["pytest", "pytest-asyncio", "ruff", "pyright"]
```

### 回归验证

```bash
# 1. 全量测试
pytest tests/ -v
# 预期：231 passed, 44 deselected

# 2. Motor 跨回调复用验证
pytest tests/test_package/test_mongodb.py -v -m integration

# 3. 异步边界验证：确认不再引用 async_runtime
grep -rn "async_runtime" src/ tests/ | grep -v "abandon" | grep -v "test"
# 期望：无输出

# 4. 启动验证
uvicorn task_planner.main.app:server --host 0.0.0.0 --port 8050

# 5. 流式任务存活验证：触发一次完整 DAG 执行，观察后台任务在回调返回后是否继续运行
```

### 0001 状态变更

在 `docs/adr/0001-async-boundary.md` 的元信息块中追加一行：

```markdown
- **Status**: Superseded by [ADR 0009](0009-fastapi-async-boundary.md)
- **Superseded Date**: 2026-11-23
```
