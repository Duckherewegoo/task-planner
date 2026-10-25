---
id: DESIGN_task_context
aliases: []
tags: []
---

# TaskContext 设计——任务生命周期的上下文抽象

> **状态**：设计文档（部分落地）
> **最近更新**：2026-10-14
> **落地部分**：TaskSession 死字段清理（见 §5）
> **未落地部分**：TaskContext Protocol（见 §7）——待多 worker 需求触发

---

## 目录

- §1 问题陈述
- §2 现状：两个对象
- §3 职责边界：为什么不合并
- §4 已知的痛点
- §5 已落地的清理（2026-10-14）
- §6 三个方向：A / B / C
- §7 方向 C 的详细设计
- §8 切换路径与触发条件
- §9 附录：历史时间线

---

## §1 问题陈述

任务规划引擎在运行时需要维护**两个维度的状态**：

1. **Agent 侧状态**——任务执行本身需要什么？（取消信号、LLM 上下文、时间基准）
2. **UI 侧状态**——用户/前端需要看到什么？（快照、节点高亮、完成标志）

当前实现将这两类状态放在**两个对象**里：

- `TaskSession`（agent 侧）
- `TaskStreamState`（UI 侧）

**问题**：两个对象的职责边界**从未在文档里正式声明过**——只有代码和零散注释。

本文档的目的：**正式定义这个边界，记录"为什么这样分"，并给出未来的合并路线**。

---

## §2 现状：两个对象

### §2.1 TaskSession

**文件**：`src/task_planner/services/agent/session.py`
**职责**：agent 执行所需的会话状态
**生命周期**：`acquire_session` 创建 → `pop_session` 销毁
**存储**：`session_store`（`InMemorySessionStore` / `RedisSessionStore`）

**字段**（2026-10-14 清理后）：

| 字段 | 类型 | 谁写 | 谁读 |
|---|---|---|---|
| `thread_id` | `str` | `__init__` | 多处 |
| `cancel_event` | `asyncio.Event` | `commands.cancel_task`（set）、`acquire_session`（clear） | `agent/stream.py`、LLM 层（通过 contextvar）、`execute.py` |
| `start_time` | `float` | `__init__` | `agent/view.py:18`（算 elapsed） |
| `config` | `@property` | 无（从 thread_id 构造） | `agent/stream.py`、`commands.py` |

**方法**：无（纯数据容器 + `config` property）。

**删除历史**（2026-10-14）：曾有以下字段，grep 确认零外部引用后删除：

- `progress_q: asyncio.Queue` —— 无消费者
- `emit(msg)` 方法 —— 唯一使用者是自身；`emit` 本身无人调
- `user_input: str` —— 只存不读
- `config` 原为字段，改 `@property`

### §2.2 TaskStreamState

**文件**：`src/task_planner/services/stream/state.py`
**职责**：UI 展示所需的流式状态
**生命周期**：`state_cleaner.register` 创建 → `state_cleaner` 后台清理
**存储**：`state_cleaner`（内存 deque + 60s 清理循环）

**字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `thread_id` | `str` | 与 TaskSession 重复，但语义独立 |
| `snapshots` | `deque[dict]` | 历史快照（`maxlen=100`） |
| `latest` | `dict` | 最新快照（UI 展示用） |
| `finished` / `cancelled` / `error` | `bool` / `str` | 生命周期标志 |
| `node_states` | `dict[str, str]` | 节点 id → 状态（`pending`/`running`/`done`/`failed`） |
| `_created_at` / `_finished_at` | `float` | 时间戳 |
| `_ttl` | `int` | 从 `_hub.dev.SESSION_TTL` 读 |
| `_lock` | `asyncio.Lock` | 保护并发读写 |
| `_task` | `asyncio.Task \| None` | **后台 runner task 的句柄——只能在这里，因为它是 stream 层创建的** |

**方法**：
- `push(snapshot)` —— 推入新快照
- `get_latest()` —— 读取最新快照
- `mark_finished()` / `mark_cancelled()` —— 生命周期标志
- `abort_worker()` —— **取消后台 task（硬取消）**
- `is_expired()` —— 清理判断

### §2.3 交互图

```
用户点"开始任务"
    │
    ▼
Dash callback（同步/异步混合）
    │
    ▼
launcher.start_stream(thread_id, ...)
    │
    ├─→ state_cleaner.register(thread_id, TaskStreamState)   ← UI 侧注册
    │
    └─→ async_runtime.submit(_bootstrap)   ← 提交到常驻 loop
            │
            ├─→ asyncio.create_task(_worker)   ← 后台 task（绑常驻 loop）
            │       │
            │       └─→ agent/stream.run_task_stream(...)
            │               │
            │               └─→ acquire_session(thread_id, resume)
            │                       │
            │                       └─→ session_store.set(...)   ← agent 侧注册
            │                               │
            │                               └─→ graph.astream(...)
            │
            └─→ _worker 每收到 event → TaskStreamState.push(snapshot)

用户点"取消"
    │
    ▼
commands.cancel_task(thread_id)
    │
    └─→ session_store.get(thread_id).cancel_event.set()   ← 只做 agent 侧
```

**关键观察**：`cancel_task` **只触及 agent 侧**——硬取消（`abort_worker`）留给 UI 层。**这是 §3 讨论的核心**。

---

## §3 职责边界：为什么不合并

### §3.1 双轨是"职责分离"，不是"技术债"

| 维度 | TaskSession | TaskStreamState |
|---|---|---|
| 服务对象 | agent / graph 执行 | 前端 / 用户 |
| 生命周期的起点 | 任务真正开始执行 | 用户点击"开始" |
| 生命周期的终点 | graph 执行完毕 | 前端确认完成 |
| 数据性质 | 执行所需（信号 / 时间基准） | 展示所需（快照 / 高亮） |
| 并发模式 | 少量写、偶读 | 频繁读写 + 锁保护 |

**两者语义不同**——同一个 `thread_id` 在两个 registry 里各存一份，是**镜像**而非**冗余**。

### §3.2 为什么不合并为一个对象

**候选合并对象**（假设叫 `TaskContext`）会同时装：

- `cancel_event` / `config` / `start_time`（agent 侧）
- `snapshots` / `node_states` / `_task`（UI 侧）

**问题 1：`asyncio.Task` 无法序列化**

`TaskStreamState._task` 是 `asyncio.Task`——**无法 pickled，无法通过 Redis 序列化**。

如果 `TaskContext` 要支持 Redis（多 worker 场景），`_task` 字段**必须排除**——但那就"没合并彻底"。

**问题 2：锁粒度冲突**

- `TaskSession`：低并发（agent 只在几个关键点访问）
- `TaskStreamState`：高并发（`_worker` 每收到 event 就 push，UI 高频轮询读）

合并后，**一个粗锁**会让 UI 读被 agent 写阻塞，或反之。**分离锁是自然的**。

**问题 3：生命周期不同步**

- `TaskSession` 在 `acquire_session` 时创建——即 **agent 真正开始执行**时
- `TaskStreamState` 在 `launcher._bootstrap` 时创建——即 **用户点击"开始"**时

**时间差**可能达几秒（`_bootstrap` 提交到常驻 loop → `_worker` 启动 → `run_task_stream` 才调 `acquire_session`）。**合并会强行拉齐，丢失语义**。

**问题 4：多 worker 场景下 `TaskSession` 可共享、`TaskStreamState` 不必**

多 worker 部署时：

- `TaskSession`（cancel_event）**必须跨 worker 共享**——否则 A worker 的取消信号 B worker 看不到
- `TaskStreamState`（快照）**不必共享**——每个 worker 各自维护自己的 UI 状态即可（用户请求路由到哪个 worker 就看哪个 worker 的）

**合并会强制两者共享或不共享——违背实际需求**。

### §3.3 结论

**双轨是正确的。** 合并会带来：

- 序列化困难
- 锁粒度冲突
- 生命周期语义混乱
- 多 worker 场景不灵活

**代价极小**（两个 registry、两套 TTL），**收益巨大**（清晰的职责、可独立演进）。

---

## §4 已知的痛点

### §4.1 `cancel_task` 无"硬取消"

**现状**：

```python
# commands.cancel_task
async def cancel_task(thread_id: str) -> bool:
    session = await get_session(thread_id)
    if session:
        session.cancel_event.set()
        return True
    return False
```

**只做**：设 `cancel_event`（协作式取消）——agent 在 `async for event` 循环里检查，**下次检查点才退出**。

**不做**：强制 `task.cancel()`（硬取消）——**正在 await 的 LLM 调用不会被打断**。

**原因**：硬取消需要访问 `TaskStreamState.abort_worker()`——但 `commands`（agent 层）**不应反向依赖 stream 层**。

**后果**：

- LLM 调用最长等 `LLM_NODE_TIMEOUT`（默认 60s）才自然结束
- 用户点"取消"到实际停止——**最长延迟 60s**

**缓解手段（当前）**：LLM 层通过 `cancel_event` 透传——`llm/core.py` 的流式分支在 `async for chunk` 里检查 `cancel_event.is_set()`。**但非流式分支不检查**——完整超时不可打断。

### §4.2 两个 registry 的 TTL 一致性

- `TaskSession`：`session_store.cleanup_stale(_SESSION_TTL)` 主动清理——**需要被调用**
- `TaskStreamState`：`state_cleaner._cleanup_loop` 每 60s 扫一次——**后台自动**

**两者都用 `_hub.dev.SESSION_TTL`**——TTL 值相同。

**但**：
- `TaskSession` 的清理**需要外部调用**（如 ASGI lifespan 或手动）
- `TaskStreamState` 的清理**自动进行**

**当前**：`cleanup_stale_sessions` **无调用方**（见 `session.py` 的 TODO）——`TaskSession` 会**泄漏**。

**影响**：单进程开发环境无实害（重启清空）；**长期运行会累积**。

### §4.3 `_task` 句柄的进程局部性

`TaskStreamState._task` 只能存在于**创建它的进程**——它是 `asyncio.Task`。

**多 worker 场景下**：
- Worker A 创建 task → Worker B 无法访问
- 用户在 Worker B 点"取消" → 拿不到 Worker A 的 task 句柄
- **跨进程取消需要消息总线**（Redis pub/sub / Kafka）

**当前**：单进程部署——无实害。

---

## §5 已落地的清理（2026-10-14）

### §5.1 删除 `TaskSession` 死字段

**背景**：grep 全项目确认零外部引用。

**删除**：

| 字段/方法 | 原位置 | 删除理由 |
|---|---|---|
| `progress_q: asyncio.Queue` | `__slots__` + `__init__` | 唯一消费者是 `emit()`，`emit()` 本身无人调 |
| `emit(msg)` 方法 | `TaskSession` | 全项目无调用（`grep -rn "\.emit(" src/` 只命中 `good_addons.EventBus.emit`） |
| `user_input: str` | `__slots__` + `__init__` | 只存不读（`grep -rn "session\.user_input" src/` 无结果） |
| `config` 字段 | `__init__` | 改为 `@property`——从 `thread_id` 现场构造，消除漂移隐患 |

**保留**：

- `cancel_event`：agent 侧取消信号
- `start_time`：`agent/view.py:18` 算 elapsed 用
- `thread_id`：基础标识

### §5.2 `config` 改 `@property`

```python
@property
def config(self) -> dict[str, Any]:
    """
    LangGraph 配置——从 thread_id 现场构造。

    历史：曾是 __init__ 里存的 dict 字段——改 @property 消除
    "config 与 thread_id 可能漂移"的隐患。
    """
    return {"configurable": {"thread_id": self.thread_id}}
```

**收益**：
- 消除"两个字段可能不一致"的隐式契约
- 无写入者——grep 确认 `session.config = ...` 零命中
- 调用方 `graph.ainvoke(..., session.config)` 零改动——property 透明

### §5.3 `acquire_session` 参数简化

```diff
 async def acquire_session(
     thread_id: str,
-    user_input: str,
     resume: bool,
 ) -> TaskSession:
```

**理由**：`user_input` 唯一用途是构造 `TaskSession`——但那个字段已删。

**调用方同步**：`agent/stream.py:38` 改为 `acquire_session(thread_id, resume)`。

### §5.4 `TaskSession` 与 `TaskStreamState` 的边界文档化

**`TaskSession` 类 docstring**：

```python
class TaskSession:
    """
    单个任务会话——**agent 侧状态**。

    与 TaskStreamState 的边界：
      - TaskSession：agent / graph 执行所需——cancel_event / start_time / config
      - TaskStreamState：UI 展示所需——snapshots / node_states / finished

    两个对象由两个 registry 分别管理（session_store / state_cleaner）——
    这是职责分离，不是技术债。详见 docs/DESIGN_task_context.md。
    """
```

**`TaskStreamState` 类 docstring**（补充）：

```python
class TaskStreamState:
    """
    单个流式任务——**UI 侧状态**。

    与 TaskSession 的边界：
      - TaskSession：agent / graph 执行所需
      - TaskStreamState：UI 展示所需 + 后台 task 句柄（_task）

    ⚠️ 硬取消（abort_worker）在 TaskStreamState——因为后台 task
       是由 stream 层创建的。
    """
```

---

## §6 三个方向：A / B / C

### §6.1 方向 A：保持双轨 + 清理

**动作**：
1. 删死字段（**已落地——见 §5**）
2. 文档化边界（**已落地**）
3. `cancel_task` 不动
4. 两个 registry 保留
5. `cleanup_stale_sessions` 加后台任务（待做，见 §4.2）

**优点**：
- 改动最小
- 现有测试全绿
- 职责清晰

**缺点**：
- `cancel_task` 无硬取消（§4.1）
- 双 registry TTL 一致性靠"两个 TTL 恰好相同"——隐式契约
- `cleanup_stale_sessions` 无调用方（潜在泄漏）

### §6.2 方向 B：合并为单一 TaskContext

**动作**：
1. 定义 `class TaskContext`——合并所有字段
2. 单一 registry
3. `cancel_task` 只碰一个对象

**优点**：
- 概念统一
- `cancel_task` 一次搞定

**缺点**：
- **`asyncio.Task` 无法 Redis 序列化** → `RedisSessionStore` 直接作废
- **锁粒度冲突**（§3.2 问题 2）
- **生命周期语义混乱**（§3.2 问题 3）
- **多 worker 场景不灵活**（§3.2 问题 4）
- Breaking change 波及：`get_stream_state` / `get_session` / `commands` / `launcher` / UI 回调
- **单进程下收益≈成本；多进程下直接废掉 Redis**

**结论**：**否决**。

### §6.3 方向 C：Protocol + 双实现

**动作**：
1. 定义 `TaskContext` Protocol——统一"任务的上下文访问"
2. `LocalTaskContext` = 当前双轨封装成一个对象（内部仍持有两个）
3. `RedisTaskContext` = 未来多 worker 用（序列化时排除 `_task`）
4. 所有调用方面向 Protocol

**优点**：
- 为方案 C（后台 loop / 多 worker）铺路
- 接口稳定——实现可替换
- 当前所有测试不变

**缺点**：
- **早期投入大**——Protocol 方法设计、迁移调用方
- 当前单进程用不上——纯粹的"为未来"

**结论**：**方向正确，时机未到**。

---

## §7 方向 C 的详细设计

> **前置条件**：需要多 worker 部署、跨进程共享任务状态、或进程重启不丢上下文。
> **在触发条件满足之前，本节只作规划，不实现。**

### §7.1 Protocol 定义

**文件**：`src/task_planner/services/agent/context.py`（新）

```python
from typing import Any, Protocol, runtime_checkable
import asyncio


@runtime_checkable
class TaskContext(Protocol):
    """
    任务上下文的统一访问接口。

    实现者：
      - LocalTaskContext：单进程——内部持有 TaskSession + TaskStreamState
      - RedisTaskContext：多 worker——只序列化 UI 侧状态

    ⚠️ 不暴露 asyncio.Task 句柄：
       - _task 是进程局部的——跨进程无意义
       - 硬取消走 request_cancel()——实现者决定如何投递
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
    async def push(self, snapshot: dict) -> None: ...

    async def get_latest(self) -> dict: ...

    async def get_node_states(self) -> dict[str, str]: ...

    # ── 生命周期 ──
    @property
    def finished(self) -> bool: ...

    @property
    def cancelled(self) -> bool: ...

    @property
    def error(self) -> str | None: ...

    async def mark_finished(self, error: str | None = None) -> None: ...

    async def mark_cancelled(self) -> None: ...

    # ── 取消（统一入口）──
    async def request_cancel(self) -> None:
        """
        请求取消任务。

        LocalTaskContext：设 cancel_event + 调 abort_worker（同一进程）
        RedisTaskContext：设 cancel_event + 发 pub/sub 消息（跨进程）
        """
        ...
```

### §7.2 LocalTaskContext 实现

```python
class LocalTaskContext:
    """
    单进程实现——内部仍持有 TaskSession + TaskStreamState。

    对外是单一对象，对内是双轨——消除调用方的"两个 registry"负担，
    同时保留双轨的序列化 / 锁 / 生命周期优势。
    """

    def __init__(
        self,
        thread_id: str,
        session: TaskSession,
        stream_state: TaskStreamState,
    ):
        self.thread_id = thread_id
        self._session = session
        self._stream = stream_state

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._session.cancel_event

    @property
    def config(self) -> dict[str, Any]:
        return self._session.config

    @property
    def start_time(self) -> float:
        return self._session.start_time

    async def push(self, snapshot: dict) -> None:
        await self._stream.push(snapshot)

    async def get_latest(self) -> dict:
        return await self._stream.get_latest()

    async def get_node_states(self) -> dict[str, str]:
        return await self._stream.get_node_states()

    @property
    def finished(self) -> bool:
        return self._stream.finished

    async def mark_finished(self, error: str | None = None) -> None:
        await self._stream.mark_finished(error)

    async def mark_cancelled(self) -> None:
        await self._stream.mark_cancelled()

    async def request_cancel(self) -> None:
        """同一进程——直接设 event + 杀 task。"""
        self._session.cancel_event.set()
        self._stream.abort_worker()
```

### §7.3 RedisTaskContext 实现

**关键难点**：

1. **`asyncio.Event` 无法跨进程**
   - 用 `threading.Event` 本地 + Redis pub/sub 广播
   - 或每次读时轮询 Redis 标志位

2. **`asyncio.Task` 无法跨进程**
   - 硬取消需要**消息总线**——Worker A 发 `{thread_id, "cancel"}` 到 Redis channel，Worker B 订阅后 `abort_worker()`

3. **`snapshots` 需要序列化**
   - 快照是纯 dict（可 JSON）——直接 Redis 存储
   - `node_states` 同理

4. **`_lock` 无法跨进程**
   - 用 Redis 分布式锁（`SET NX EX`）

**实现示意**：

```python
class RedisTaskContext:
    """多 worker 实现——UI 侧状态走 Redis，agent 侧本地。"""

    def __init__(self, thread_id: str, redis_client):
        self.thread_id = thread_id
        self._redis = redis_client
        # agent 侧的 cancel_event 仍在本地——通过 pub/sub 同步
        self._cancel_event = asyncio.Event()
        self._subscribe_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动 pub/sub 订阅——接收跨进程取消信号。"""
        self._subscribe_task = asyncio.create_task(self._subscribe_cancel())

    async def _subscribe_cancel(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(f"cancel:{self.thread_id}")
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                self._cancel_event.set()
                break

    async def request_cancel(self) -> None:
        """发广播——所有 worker 都能收到。"""
        await self._redis.publish(f"cancel:{self.thread_id}", "1")
        # 本进程也设——避免等 pub/sub 往返
        self._cancel_event.set()
```

### §7.4 调用方迁移

**`commands.cancel_task`**：

```python
async def cancel_task(thread_id: str) -> bool:
    ctx = await get_task_context(thread_id)  # 新入口——不再是 get_session
    if ctx is None:
        return False
    await ctx.request_cancel()  # 统一入口——内部处理本地 / 跨进程
    return True
```

**UI 层读快照**：

```python
# 旧
state = await state_cleaner.get(thread_id)
latest = await state.get_latest()

# 新
ctx = await get_task_context(thread_id)
latest = await ctx.get_latest()
```

**launcher**：

```python
# 旧
state = TaskStreamState(thread_id)
await state_cleaner.register(thread_id, state)

# 新
ctx = await create_task_context(thread_id)  # 工厂函数——按环境返回 Local/Redis
```

---

## §8 切换路径与触发条件

### §8.1 触发条件

**任一满足才做**：

| 条件 | 检测方式 |
|---|---|
| 需要多 worker 部署 | `gunicorn --workers N` / `uvicorn --workers N` |
| 需要跨进程共享任务状态 | 用户 A 在 Worker 1 看到 Worker 2 创建的任务 |
| 需要进程重启后任务上下文不丢 | 硬性业务需求 |
| UI 层独立部署（前后端分离） | 前端独立于后端进程 |

### §8.2 切换步骤

**阶段 1（准备）**：定义 Protocol
- 新增 `services/agent/context.py`——定义 `TaskContext`
- **不改调用方**——只定义

**阶段 2（本地化）**：实现 LocalTaskContext
- `LocalTaskContext` 包装现有双轨
- `get_task_context(thread_id)` 工厂返回 `LocalTaskContext`
- 逐个调用方迁移——**旧 API（`get_session` / `state_cleaner.get`）保留为兼容层**
- 跑全测试——**这是最关键的验证点**

**阶段 3（跨进程）**：实现 RedisTaskContext
- `RedisTaskContext` 实现
- 工厂根据配置（`CONTEXT_BACKEND=redis`）切换
- **此时才需要** Redis pub/sub + 分布式锁

**阶段 4（收尾）**：删兼容层
- 所有调用方面向 `get_task_context`
- 删除 `get_session` / `state_cleaner.get` 的公开 API（或标记 deprecated）

### §8.3 每阶段的验证

| 阶段 | 验证 |
|---|---|
| 1 | 无——纯定义 |
| 2 | `pytest tests/ -qv` 全绿 + 手动跑一次任务 |
| 3 | 多 worker 部署冒烟（2 worker + 一个任务 + 跨 worker 取消） |
| 4 | 全回归 + 生产环境观察 |

### §8.4 不做的事

- **不合并 TaskSession 与 TaskStreamState**（§6.2 已分析）
- **不在单进程下启用 RedisTaskContext**（无用+引入新依赖）
- **不在没有触发条件时提前做**（违反 YAGNI）

---

## §9 附录：历史时间线

| 日期 | 事件 | 落地 |
|---|---|---|
| 2026-10-13 | 引入 AsyncRuntime 常驻 loop——修复 Dash 一次性 loop | ✅ |
| 2026-10-13 | 发现 `TaskSession` 死字段（`progress_q` / `emit` / `user_input`） | 记录 |
| 2026-10-14 | 清理死字段 + `config` 改 `@property` | ✅ |
| 2026-10-14 | 撰写本设计文档 | ✅ |
| 待定 | `cleanup_stale_sessions` 加后台任务 | ⏳ |
| 待定 | 引入 `TaskContext` Protocol | ⏳（触发条件见 §8.1） |
| 待定 | 实现 `LocalTaskContext` | ⏳ |
| 待定 | 实现 `RedisTaskContext` | ⏳ |

---

## 相关文档

- `ARCHITECTURE_FUTURE.md`——UI/任务层边界、Dash loop 行为实测
- `CHANGELOG.md`——各模块变更历史
- `services/agent/session.py`——TaskSession 实现
- `services/stream/state.py`——TaskStreamState 实现
