---
id: DESIGN_task_context_v2
aliases: [TaskContext 设计 v2]
tags: [architecture, async, session, stream, design]
---

# TaskContext 设计 v2 —— 双轨状态边界（FastAPI backend 时代）

> **状态**：v2 生效中（2026-11-25）
> **取代**：本文档 v1（2026-10-14，AsyncRuntime 时代）——v1 的核心前提已随 ADR-0009 作废，**不再维护**，仅作历史参考
> **配套 ADR**：[ADR-0009 FastAPI backend 统一异步边界](adr/0009-fastapi-async-boundary.md)（架构事实源）
> **v2 定位**：ADR-0009 负责"架构为什么这样"；本文档只负责"两个状态对象为什么分开、协议长什么样"。**凡 ADR 已述的架构演进，本文一律引用不复述。**

---

## 目录

- §1 问题陈述（不变）
- §2 现状：两个对象（**已按 2026-11-25 代码重写**）
- §3 职责边界：为什么不合并（**论证重排**）
- §4 已知痛点（**大幅收敛：v1 的 3 条中 1 条已解、1 条降级**）
- §5 已落地清理（v1 §5 保留 + 2026-11-25 增补）
- §6 三个方向：A / B / C（**A 已选定并落地**）
- §7 TaskContext Protocol 草案（**按真实签名重写**）
- §8 切换路径与触发条件（**改为引用 ADR-0009 Revocation**）
- §9 附录：时间线

---

## §1 问题陈述

任务规划引擎运行时需维护**两个维度的状态**：

1. **Agent 侧状态**——任务执行本身需要什么？（取消信号、时间基准、LangGraph config）
2. **UI 侧状态**——用户/前端需要看到什么？（快照、节点高亮、完成标志）

当前实现放在**两个对象**里：`TaskSession`（agent 侧）、`TaskStreamState`（UI 侧）。

**问题**：两者边界此前从未正式声明，只有代码和零散注释。本文档定义这条边界、记录"为什么这样分"，并给出未来的统一访问入口路线。

---

## §2 现状：两个对象

### §2.1 TaskSession（agent 侧）

**文件**：`src/task_planner/services/agent/session.py`
**职责**：agent / graph 执行所需状态
**生命周期**：`acquire_session` 创建 → `pop_session` 销毁（stream 层 `finally` 调用）
**存储**：`session_store`（`InMemorySessionStore` / `RedisSessionStore`，由 `SESSION_STORE_TYPE` 工厂选择）

| 字段 | 类型 | 谁写 | 谁读 |
|---|---|---|---|
| `thread_id` | `str` | `__init__` | 多处 |
| `cancel_event` | `asyncio.Event` | `commands.cancel_task`（set）、`acquire_session`（**`clear()` 复用同一对象**） | `agent/stream.py`、LLM 层（经 `contextvar`）、`execute.py` |
| `start_time` | `float` | `__init__` | `agent/view.py::extract_snapshot`——`elapsed = time.time() - session.start_time`，以 `round(elapsed, 1)` 注入快照（行号随文件头浮动，grep `elapsed = time.time()` 定位） |
| `config` | `@property` | 无（从 `thread_id` 现场构造） | `agent/stream.py`、`commands.py` |

**方法**：无（纯数据容器 + `config` property）。`__slots__` 含 `__weakref__`。

**TTL 语义**：基于**创建时刻**——`cleanup_stale_sessions()` 用 `_SESSION_TTL`（默认 3600s）从 `start_time` 起算。

**⚠️ 不持有 runner task 句柄**。这是 §3 与 §4.1 的关键：硬取消的能力不在这一侧。

### §2.2 TaskStreamState（UI 侧）

**文件**：`src/task_planner/services/stream/state.py`
**职责**：UI 展示所需流式状态 + 后台 task 句柄
**生命周期**：`launcher` 创建并 `state_cleaner.register` → `state_cleaner` 清理
**存储**：`state_cleaner`（内存 + 清理循环）

| 字段 | 类型 | 说明 |
|---|---|---|
| `thread_id` | `str` | 与 TaskSession 同名，语义独立 |
| `snapshots` | `deque[Snapshot]` | 历史快照（`maxlen=100`，`Snapshot = dict[str, Any]`） |
| `latest` | `Snapshot` | 最新快照（UI 展示）；`push` 时继承上一帧的 `nodes`/`edges`/`direct_response`/`flowchart_html`/`task_id` |
| `finished` / `cancelled` / `paused` | `bool` | 生命周期标志；**`paused` 与 `finished` 互斥** |
| `next_nodes` | `list[str]` | interrupt 后的可选继续节点 |
| `error` | `str \| None` | 错误信息 |
| `node_states` | `dict[str, str]` | 节点 id → `pending`/`running`/`done`/`failed`/`skipped` |
| `_created_at` / `_last_active_at` / `_finished_at` / `_paused_at` | `float \| None` | 时间戳 |
| `_ttl` | `int` | **构造时**从 `_hub.dev.SESSION_TTL` 读一次（v1 误记为"每次动态读"） |
| `_lock` | `asyncio.Lock` | 保护 `latest` / `node_states` / 标志位 |
| `_task` | `asyncio.Task[None] \| None` | **后台 runner 句柄——只能在这里，因为 task 由 stream 层创建** |

**方法**：`push(snapshot)`、`get_latest()`、`get_node_states()`、`mark_finished() / mark_cancelled() / mark_error()`、**`mark_paused(next_nodes=...)`**、`is_expired()`、`age_since_created()`、`set_task()`、`abort_worker()`。

⚠️ **没有 `pause()` / `resume()` 这两个方法名**，恢复走两条不同路径：

| 场景 | 入口 | 语义 |
|---|---|---|
| 图停在 interrupt，流层挂起 | `TaskStreamState.mark_paused(next_nodes=...)`（`state.py`，由 `launcher._worker` 调） | 只改状态，不动图 |
| 流式恢复执行 | `launcher.resume_stream()`（`launcher.py`） | stream 层重新拉起 `_worker` |
| API / UI 直接命令 | `commands.resume_task()` / `modify_task()` / `retry_node_cmd()` | 走 `graph.ainvoke(Command(resume=...))` |

后两条都是恢复，但一条在 stream 层、一条在 agent 层，**§7 的 Protocol 只暴露 `mark_paused`，不暴露恢复**——恢复是流程动作，不是状态写入。

**TTL 语义**：基于 **`_last_active_at`**（最后活动时刻），而非 `_created_at`。
原因：paused 时用户可能思考数分钟才点 resume，按 created 起算会把**正在等待交互的任务误判过期**。

### §2.3 交互图（FastAPI backend 版）

```
用户点「开始任务」
    │
    ▼
Dash async callback（跑在 uvicorn 持久 loop 上）
    │
    ├─→ launcher.start_stream(thread_id, ...)
    │       ├─→ TaskStreamState(thread_id) + state_cleaner.register(...)
    │       └─→ asyncio.create_task(_worker)        ← 原生 create_task，跨回调存活
    │               │
    │               ├─→ state.set_task(task)        ← 句柄登记在 UI 侧
    │               └─→ agent/stream.run_task_stream(...)
    │                       └─→ acquire_session(thread_id, resume)
    │                               └─→ session_store.set(...)
    │
    └─→ _worker 每收到 event → TaskStreamState.push(snapshot)

用户点「取消」
    │
    ▼
commands.cancel_task(thread_id)
    ├─→ session.cancel_event.set()                  ← 协作式（agent 侧）
    └─→ _aborts[thread_id]() → state.abort_worker() ← 硬取消（组合层注入，见 §4.1）

前端每 1000ms 轮询 → get_latest() → node_states / finished / paused
```

**关键观察**：取消是**两条通道**——协作式信号走 agent 侧，硬打断走 UI 侧。
`commands` 不 import stream 层，靠**组合层注入**拿到 `abort_worker`（§4.1）。

---

## §3 职责边界：为什么不合并

### §3.1 双轨是职责分离，不是技术债

| 维度 | TaskSession | TaskStreamState |
|---|---|---|
| 服务对象 | agent / graph 执行 | 前端 / 用户 |
| 生命周期起点 | 任务真正开始执行 | 用户点击「开始」 |
| 生命周期终点 | graph 执行完毕 | 前端确认完成 |
| 数据性质 | 执行所需（信号 / 时间基准） | 展示所需（快照 / 高亮 / 标志） |
| 并发模式 | 少量写、偶读 | 频繁读写 + 锁保护 |
| TTL 基准 | 创建时刻 | **最后活动时刻** |

同一 `thread_id` 在两个 registry 里各存一份，是**镜像**而非冗余。

### §3.2 为什么不合并为一个对象

**问题 1：`asyncio.Task` 是 loop-local 的（v1 论证有误，此处更正）**

v1 说"`_task` 无法 pickle，所以不能合并"。这个论证**不成立**：单进程本就不需要序列化；而多 worker 下**即使把 `_task` 排除掉**，Worker B 也无法 `cancel()` Worker A 的 task——跨 loop 调用直接抛错。

真正的障碍是：**task 绑定创建它的 event loop**。FastAPI backend 下这个 loop 是 uvicorn 的主 loop，每 worker 一个。因此跨进程取消**必须走消息总线**（Redis pub/sub），而不是"把字段剔除"。§7 的 Protocol 正是这样设计的：不暴露 task 句柄，只暴露 `request_cancel()`。

**问题 2：锁粒度冲突（成立）**

- `TaskSession`：低并发（agent 只在几个关键点访问）
- `TaskStreamState`：高并发（`_worker` 每收到 event 就 `push`，UI 每 1000ms 轮询读）

合并后一把粗锁会让 UI 读被 agent 写阻塞，或反之。**分离锁是自然的**。

**问题 3：生命周期不同步（成立，且比 v1 描述的更严重）**

- `TaskSession` 在 `acquire_session` 时创建（agent 真正开始执行）
- `TaskStreamState` 在 `launcher` 里创建（用户点击"开始"）

时间差可达数秒。**更关键的是 TTL 基准不同**（§2.1 / §2.2）：paused 等用户确认的任务，UI 侧因 `_last_active_at` 刷新而永不过期，agent 侧却按 `start_time` 倒计时。 session 先过期后，`acquire_session` 走 `get` 拿不到会**静默新建**——用户等久了点"继续"，等于换了个 session。这是当前真实存在的不一致，**合并会强行拉齐并掩盖它，分离才让它可被单独修复**。

**问题 4：多 worker 下共享需求不同（v1 结论保留，落地状态待验证）**

- `TaskSession`（cancel_event）跨 worker 共享才有意义
- `TaskStreamState`（快照）不必共享——请求路由到哪个 worker 就看哪个 worker 的

✅ **落地状态（已确认）**：`RedisSessionStore` **全异步**——`get` / `set` / `pop` / `cleanup_stale` / `count` / `exists` 均为 `async def`，与 `session_store/protocol.py` 的异步 Protocol 匹配；客户端用 `from redis.asyncio import Redis`（`redis-py>=5` 的同步 `Redis` 类仍在，但本实现不走它）。**默认不启用**：需 `SESSION_STORE_TYPE=redis` + `REDIS_URL`，缺省为 `memory`。启用后多 worker 通过 Redis 共享会话，过期靠原生 TTL。

三条必须在迁移前知道的语义差异：

| 项 | `InMemorySessionStore` | `RedisSessionStore` |
|---|---|---|
| `cleanup_stale()` | 真扫描删除 | **no-op（返回 0）**，过期完全交给 Redis TTL |
| 重复 `set` 同一 thread | 保留原 `created_at` | **重置 TTL**（会话被"续命"） |
| 可存对象 | 任意 Python 对象（含 `asyncio.Event`） | **仅可序列化对象**——`asyncio.Task` / `Event` / `Lock` 一律不能进 |

第三行是 §3.2 问题 1 的落地版：**多 worker 下 `TaskSession` 能进 Redis，前提是它保持"纯数据 + 无 asyncio 原语"**——现在只有 `thread_id` / `start_time` / `cancel_event`，其中 `cancel_event` 就是那个**不能跨进 Redis 的字段**。这直接决定 §8 阶段 3 的工作量：cancel 必须走 pub/sub（§7 `request_cancel`），不能指望共享 `Event`。

### §3.3 结论

**双轨是正确的。** 合并的代价：loop-local 语义被掩盖、锁粒度冲突、生命周期与 TTL 基准混乱、多 worker 共享策略无法分别选择。

代价是两个 registry + 两套 TTL，收益是职责清晰、可独立演进、不一致可被观测。

---

## §4 已知痛点

### §4.1 `cancel_task` 硬取消 —— 【✅ 已落地：组合层注入 + 双通道】

**现状**：`cancel_task` 只设 `cancel_event`（协作式）。`asyncio.sleep` 与 `await graph.astream(...)` 都不检查该标志，因此一次 LLM 调用最长要等 `LLM_NODE_TIMEOUT`（默认 60s）才自然结束。

**v1 的误判**：v1 说"硬取消需要访问 `abort_worker`，但 commands（agent 层）不应反向依赖 stream 层，所以做不到"。

**这个结论是错的**。项目里已有先例：`app.py` 的 shutdown 同时 import `core.db` 与 `services.stream.cleaner`，说明**组合层本来就可以同时碰两边**。硬取消不需要 commands 反向依赖 stream，只需要组合层注入：

```python
# launcher 创建状态后登记能力
from task_planner.services.agent import commands as _commands

_commands.register_abort(thread_id, state.abort_worker)

# commands.cancel_task 内
_abort = _aborts.pop(thread_id, None)
if _abort is not None:
    _abort()  # 硬打断正在 await 的 IO
session.cancel_event.set()  # 协作式信号照旧
```

**收益**：取消延迟从「最长 60s」降到「一个 event loop tick」。
**约束**：`_aborts` 必须与 `thread_id` 生命周期绑定，`pop_session` 时一并回收，否则泄漏。

✅ **已落地**：`commands._aborts: dict[str, Callable[[], None]]` + `register_abort()` / `unregister_abort()`；`launcher._run_worker` 在 `task = asyncio.create_task(_worker())`、`state.set_task(task)` 之后调 `_commands.register_abort(thread_id, state.abort_worker)`，`finally` 里 `unregister_abort(thread_id)`。`cancel_task` 的顺序是**先硬取消、再协作取消**：

```python
_abort = _aborts.pop(thread_id, None)  # 1. 硬取消：打断正在 await 的 IO
if _abort:
    _abort()  #    TaskStreamState.abort_worker() → task.cancel()
session = await get_session(thread_id)
if session:
    session.cancel_event.set()  # 2. 协作取消：graph / LLM 层的检查点
```

**v1 记录的"最长 60s 延迟"由此消除**：不再等 `LLM_NODE_TIMEOUT` 自然结束。非流式 LLM 分支原本不检查 `cancel_event`（v1 痛点），现在由 `task.cancel()` 直接打穿。

⏭ **残留待办（收窄为两项）**：

1. **`_aborts` 的进程局部性**：与 §4.3 同源——`_aborts` 是进程内 dict，多 worker 下请求落到别的 worker 仍拿不到句柄，跨进程仍需 pub/sub（§7 `request_cancel`）。
2. **注销路径要覆盖所有退出分支**：`unregister_abort` 只在 `_run_worker` 的 `finally` 里调；若 `_bootstrap` 在 `create_task` 之前就抛异常，注册表不会留脏项（因为还没注册），但 `pop_session` 侧不负责回收——**注销责任单一归属 stream 层**，这条约束要守住。

### §4.2 两个 registry 的 TTL 一致性 —— 【v1 的"泄漏"已解，风险改写】

**v1 说**：`cleanup_stale_sessions` 无调用方，`TaskSession` 会泄漏。

**已解，但有入口边界**：`app.py::_register_lifespan_handlers` 的 `_on_startup` 起 `_cleanup_loop` 后台任务，每 `_SESSION_CLEANUP_INTERVAL`（现 300s，硬编码兜底；`DESIGN NOTE(hot-reload)` 要求挪进 `hub.dev` 支持热重载）调一次 `cleanup_stale_sessions()`。

⚠️ **这条调用链只在 FastAPI backend 下存在**。`_register_lifespan_handlers` 挂的是 `app.server.router` 的 startup/shutdown 事件，只有 `main/app.py` 这条入口会调它；`config_ui/standalone_app.py` 若走纯 Dash / Werkzeug backend，**startup 事件不触发，`cleanup_stale_sessions` 在那里仍无调用方**，长跑依然泄漏。所以准确表述是：

> **主入口（uvicorn + FastAPI）不再泄漏；非 FastAPI 入口需自行注册清理任务或手动调用，否则保留 v1 的泄漏风险。**

**残留风险（改写后）**：不是"泄漏"，而是**两套 TTL 基准不同源**（§3.2 问题 3）。
- agent 侧：`_SESSION_TTL` 从 `start_time` 起算
- UI 侧：`_ttl` 从 `_last_active_at` 起算，且是**构造时读一次**，热重载不改已有任务

**这不是 bug，是设计差异**——但必须有断言守住，否则哪天有人"顺手统一"就会让 paused 任务被误清。建议在 `cleanup_stale_sessions` 与 `is_expired` 各加一行注释互相指向。

### §4.3 `_task` 句柄的进程局部性（不变）

`TaskStreamState._task` 只能存在于创建它的进程。多 worker 下 Worker B 拿不到 Worker A 的句柄，跨进程取消需消息总线（Redis pub/sub）。

**当前单进程部署无实害**。这条与 ADR-0009 Revocation §3 是同一件事，不另立触发条件。

### §4.4 【新增】`_resume_locks` 只增不减

`commands._resume_locks` 按 `thread_id` 缓存 `(Lock, loop)`，`release_thread_lock()` 目前**无人调用**。`pop_session` 已删 session，锁却留着——长跑会缓慢累积。

**修法**：在 stream 层 `finally` 里 `pop_session` 之后顺手调一次 `release_thread_lock(thread_id)`。

---

## §5 已落地的清理

### §5.1 `TaskSession` 死字段清理（2026-10-14，grep 确认零引用后删除）

| 字段/方法 | 删除理由 |
|---|---|
| `progress_q: asyncio.Queue` | 唯一消费者是 `emit()`，`emit()` 本身无人调 |
| `emit(msg)` | 全项目无调用 |
| `user_input: str` | 只存不读 |
| `config` 字段 | 改 `@property`，从 `thread_id` 现场构造，消除漂移隐患 |

### §5.2 `acquire_session` 参数简化

签名从 `(thread_id, user_input, resume)` 收敛为 `(thread_id, resume)`。

### §5.3 边界文档化

`TaskSession` / `TaskStreamState` 的类 docstring 各自声明边界（内容随 v2 更新，指向本文档）。

### §5.4 【新增】cog 配置系统去重（2026-11-25）

`schema.yaml` 与 `sections/*.py` 原有 **7 个配置项双写**（`MOCK_RESPONSE_PREFIX`、`DASHSCOPE_TIMEOUT`、`LLM_STREAM`、`MONGO_TIMEOUT_MS`、`RENDER_BACKGROUND`、`REFINE_CONCURRENCY`、`SENSITIVE_MASK_CHAR`）。

装配顺序是 **YAML 先装、代码后装、`has(name)` 跳过**，因此生效的永远是 YAML 那份，代码里的 `value=` 是**死配置**——改了不生效且无任何提示。

**处理**：
- 7 条从代码 section 删除，YAML 作为唯一来源
- 例外：`MOCK_RESPONSE_PREFIX` 从 YAML 删除，只留代码（它是 mock 模块与 `json_utils` 的内部约定，不是运维旋钮）
- `register_into` 的跳过分支补 `logger.warning`，让"影子条目"可见
- `sections/__init__.py` 从空文件改为显式导入 7 个子模块——**原来是空的，导致代码内置项一条都没注册且不报错**
- `registry.py` 去掉对 `section` 的直接 import（循环导入），改鸭子类型检查
- CI 新增 `scripts/check_config_drift.py`：配置名全局唯一 + 密钥必须 `from_env: true`

### §5.5 【新增】`TaskStreamState` 状态机修正（2026-11-25）

- `node_states` 终态保护：`done` / `failed` / `skipped` 不被回退为 `running`（原代码只挡了 `done`，节点失败后 LangGraph 重放会先闪 running 再回 failed）
- `node_results` 判定从 `is True` / `is False` 放宽为真值判断——真实 dict 从 MongoDB / JSON 绕一圈出来值是 `1`/`0`，原写法会漏判
- 10 条状态机断言覆盖：快照继承、终态保护、并发 push 无丢失、deque 上限、副本隔离、`abort_worker` 真实取消

---

## §6 三个方向：A / B / C

### §6.1 方向 A：保持双轨 + 清理 —— ✅ **已选定并落地**

动作：删死字段、文档化边界、两个 registry 保留、`cleanup_stale_sessions` 接后台任务。

**优点**：改动最小；现有测试全绿；职责清晰。
**代价**：取消需组合层注入（§4.1）；双轨 TTL 一致性靠注释 + 断言守住（§4.2）。

### §6.2 方向 B：合并为单一 TaskContext —— ❌ 否决

**否决理由（v2 重排）**：

1. **`asyncio.Task` 是 loop-local**（§3.2 问题 1）——排除 `_task` 字段也不解决跨进程取消
2. **锁粒度冲突**（§3.2 问题 2）
3. **TTL 基准不同**（§3.2 问题 3）——合并会掩盖 paused 任务的过期不一致
4. **多 worker 共享策略无法分别选择**（§3.2 问题 4）
5. Breaking change 波及 `get_stream_state` / `get_session` / `commands` / `launcher` / UI 回调
6. 单进程下收益≈成本；多进程下直接废掉 Redis session store 的可用性

### §6.3 方向 C：Protocol + 双实现 —— ⏸ 方向正确，时机未到

定义 `TaskContext` Protocol，所有调用方面向接口；`LocalTaskContext` 封装双轨，`RedisTaskContext` 供多 worker。

**优点**：接口稳定、实现可替换、为多 worker 铺路。
**代价**：早期投入大，当前单进程用不上。

**落地策略**：不单独立项，**随多 worker 需求一起做**（§8）。在此之前 §7 只作规划。

---

## §7 TaskContext Protocol 草案（按真实签名重写）

> **前置条件**：需要多 worker 部署、跨进程共享任务状态、或进程重启不丢上下文。
> **触发条件见 ADR-0009 Revocation §3，本节不另立清单。**
> **在触发条件满足前，本节只作规划，不实现。**

### §7.1 与 v1 草案的差异

| v1 写的 | v2 按真实代码更正为 |
|---|---|
| `push(snapshot) -> None` 是 async | **同步**语义（`TaskStreamState.push` 是 `async def`，但内部无 IO；Protocol 保持 `async def` 以留实现空间） |
| `mark_finished(error)` 单方法 | **三个方法**：`mark_finished()` / `mark_cancelled()` / `mark_error()` |
| `abort_worker()` 直接暴露 | **不暴露**——换成 `request_cancel()`，实现者决定如何投递 |
| 只提 `InMemorySessionStore` / `RedisSessionStore` | **复用已有** `session_store/protocol.py`，不新造 |
| `_ttl` 未说明 | **必须是 property 动态读** `_hub.dev.SESSION_TTL`，不能固化成构造参数（否则热重载失效） |

### §7.2 Protocol 定义

**文件**：`src/task_planner/services/agent/context.py`（新）

```python
from typing import Any, Protocol, runtime_checkable
import asyncio


@runtime_checkable
class TaskContext(Protocol):
    """任务上下文的统一访问接口。

    实现者：
      - LocalTaskContext：单进程——内部仍持有 TaskSession + TaskStreamState
      - RedisTaskContext：多 worker——只序列化 UI 侧状态

    ⚠️ 不暴露 asyncio.Task 句柄：task 是 loop-local 的（§3.2 问题 1），
       跨进程无意义。硬取消一律走 request_cancel()。
    """

    thread_id: str

    # ── agent 侧 ──
    @property
    def cancel_event(self) -> asyncio.Event: ...

    @property
    def config(self) -> dict[str, Any]: ...

    @property
    def start_time(self) -> float: ...

    # ── UI 侧 ──
    async def push(self, snapshot: dict[str, Any]) -> None: ...

    async def get_latest(self) -> dict[str, Any]: ...

    async def get_node_states(self) -> dict[str, str]: ...

    @property
    def paused(self) -> bool: ...  # v2 新增：paused 与 finished 互斥

    # ── 生命周期 ──
    @property
    def finished(self) -> bool: ...

    @property
    def cancelled(self) -> bool: ...

    @property
    def error(self) -> str | None: ...

    async def mark_finished(self) -> None: ...
    async def mark_cancelled(self) -> None: ...
    async def mark_error(self, message: str) -> None: ...

    # ── 取消（统一入口）──
    async def request_cancel(self) -> None:
        """LocalTaskContext：设 cancel_event + abort_worker()（同一进程）
        RedisTaskContext：设 cancel_event + pub/sub 广播（跨进程）
        """
        ...
```

### §7.3 LocalTaskContext 实现要点

```python
class LocalTaskContext:
    """单进程实现——内部仍持有 TaskSession + TaskStreamState。

    对外是单一对象，对内是双轨：既消除调用方的"两个 registry"负担，
    又保留双轨在 loop-local / 锁粒度 / TTL 基准上的优势（§3.2）。
    """

    def __init__(self, thread_id: str, session: TaskSession, stream: TaskStreamState):
        self.thread_id = thread_id
        self._session = session
        self._stream = stream
        self._abort: Callable[[], None] | None = None  # 组合层注入（§4.1）

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._session.cancel_event

    # ... config / start_time / push / get_latest / get_node_states 透传 ...

    async def request_cancel(self) -> None:
        """同一进程——直接设 event + 杀 task。"""
        self._session.cancel_event.set()
        if self._abort is not None:
            self._abort()
```

**必须保留的语义**：
- `config` / `start_time` 透传 `TaskSession` 的 property，不要拷贝成字段
- `push` / `get_latest` 透传时**不要绕过 `_lock`**
- 实现 `RedisTaskContext` 时，`cancel_event` 无法跨进程——用本地 `asyncio.Event` + Redis pub/sub 广播（§7.4）

### §7.4 RedisTaskContext 难点

| 难点 | 处理 |
|---|---|
| `asyncio.Event` 无法跨进程 | 本地 `asyncio.Event` + Redis pub/sub 广播；或读时轮询 Redis 标志位 |
| `asyncio.Task` 无法跨进程 | 不传句柄。Worker A 发 `{thread_id, "cancel"}` 到 channel，Worker B 订阅后本地 `abort_worker()` |
| `snapshots` / `node_states` | 纯 dict，可 JSON 直接存取 |
| `_lock` 无法跨进程 | Redis 分布式锁（`SET NX EX`） |
| **同步 redis 客户端阻塞 loop** | **必须用 `redis>=5` 的 async 客户端**；现有 `RedisSessionStore` 是同步接口，迁移时需一并 async 化（§3.2 问题 4） |

### §7.5 调用方迁移

```python
# commands.cancel_task
async def cancel_task(thread_id: str) -> bool:
    ctx = await get_task_context(thread_id)  # 新入口，取代 get_session
    if ctx is None:
        return False
    await ctx.request_cancel()  # 统一入口，本地/跨进程由实现决定
    return True


# UI 读快照
ctx = await get_task_context(thread_id)
latest = await ctx.get_latest()

# launcher
ctx = await create_task_context(thread_id)  # 工厂：按 CONTEXT_BACKEND 返回 Local/Redis
```

---

## §8 切换路径与触发条件

> **触发条件统一引用 [ADR-0009 Revocation](adr/0009-fastapi-async-boundary.md) §3**（跨进程/跨机器任务调度需求）。
> 本节只记阶段划分，**不重复条件清单**——两处各维护一份必然漂移。

| 阶段 | 动作 | 验证 |
|---|---|---|
| 1 准备 | 新增 `services/agent/context.py`，只定义 Protocol，**不改调用方** | 无（纯定义） |
| 2 本地化 | `LocalTaskContext` 包装双轨；`get_task_context()` 工厂；调用方逐个迁移；旧 API 保留为兼容层 | 全量测试 + 手动跑一次任务（含 paused → resume） |
| 3 跨进程 | `RedisTaskContext`；工厂按 `CONTEXT_BACKEND=redis` 切换；**此时才需要** pub/sub + 分布式锁 + Redis 客户端 async 化 | 2 worker 部署冒烟：一个任务跨 worker 取消 |
| 4 收尾 | 删兼容层（`get_session` / `state_cleaner.get` 标 deprecated 后移除） | 全回归 + 生产观察 |

**阶段 2 是最关键的验证点**——它不改变行为，只改变访问路径，因此任何测试失败都意味着 Protocol 漏了语义。

### §8.1 不做的事

- 不合并 `TaskSession` 与 `TaskStreamState`（§6.2）
- 不在单进程下启用 `RedisTaskContext`（无用且引入新依赖）
- 没有触发条件不提前做（YAGNI）
- 不在本文档里另立触发条件清单（引用 ADR-0009）

---

## §9 附录：时间线

| 日期 | 事件 | 落地 |
|---|---|---|
| 2026-09-28 | ADR-0002 全链路异步（Motor + AsyncOpenAI） | 在 WSGI 下实测失败 |
| 2026-10-13 | ADR-0001 异步边界分层 + `async_runtime` 常驻 loop | ✅ 后作废 |
| 2026-10-13 | 发现 `TaskSession` 死字段 | 记录 |
| 2026-10-14 | 清理死字段 + `config` 改 `@property` | ✅ |
| 2026-10-14 | 撰写本文档 v1 | ✅ |
| 2026-11-11 | FastAPI backend 迁移触发条件明确 | — |
| 2026-11-23 | **ADR-0009** FastAPI + uvicorn + Motor 统一异步边界 | ✅ |
| 2026-11-23 | 移除 `async_runtime`，全链路适配 | ✅ |
| 2026-11-25 | Lifespan 钩子落地：300s 清理循环 + 按序兜底关闭 | ✅ |
| 2026-11-25 | cog 配置去重（7 条双写）+ 空 `__init__` 导致代码项未注册 | ✅ |
| 2026-11-25 | `TaskStreamState` 终态保护 + node_results 真值判断 | ✅ |
| 2026-11-25 | **本文档 v2**：架构叙述改为引用 ADR-0009；§3.2 论证重排；§4 收敛；§7 按真实签名重写 | ✅ |
| 2026-11-25 | §4.1 硬取消组合层注入落地（`commands._aborts` + `launcher._run_worker` 注册/注销，`cancel_task` 双通道） | ✅ |
| 待定 | §4.4 `_resume_locks` 回收 | ⏳ |
| 待定 | `TaskContext` Protocol（触发条件见 ADR-0009 §3） | ⏳ |

---

## 相关文档

- `adr/0009-fastapi-async-boundary.md` —— **架构事实源**：异步边界决策与撤销条件
- `adr/0002-full-chain-async.md` / `adr/0001-async-boundary.md` —— 被取代的决策链
- `docs/ARCH_FUTURE.md` —— 架构演进与 UI/任务层边界（旧名 `ARCHITECTURE_FUTURE.md`）
- `docs/CHANGELOG.md` —— 各模块变更历史
- `services/agent/session.py` —— `TaskSession` 实现
- `services/stream/state.py` —— `TaskStreamState` 实现
- `services/session_store/protocol.py` —— 已有 SessionStore 协议（§7 应复用）
