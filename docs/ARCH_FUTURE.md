---
id: ARCH_FUTURE
aliases: []
tags: []
---

# 架构现状与演进路径

> 本文档记录**关键架构决策的"为什么"**——不是"我们做了什么"（那在 git log），
> 而是"为什么这样做、什么条件下要改、改的时候动哪几处"。
>
> 最近更新：2026-10-13（Dash loop 行为实测 + 同步客户端迁移）

---

## 一、UI 层与任务层的边界

### 现状

| 层 | 技术 | 驱动方式 | 是否异步 |
|---|---|---|---|
| UI | Dash 4.4.1（Flask WSGI 底层） | `asgiref.async_to_sync` | 声明 async，实际同 thread 同步执行 |
| 任务 | LangGraph | 在 Dash 回调里 `await` 驱动 | 节点是 async def，但由 Dash 的 loop 调度 |
| DB | pymongo（同步） | 直接调用 | 否 |
| LLM | openai（同步） | 直接调用 | 否 |

### Dash 的 event loop 行为（2026-10-13 实测）

Dash 4.4.1 + `dash[async]` 的 Flask backend 通过 `asgiref.async_to_sync`
驱动每个 async 回调。**实测发现：同 thread 内 event loop 的 id 会变化。**

证据：连续触发同一回调，`id(asyncio.get_running_loop())` 序列 = A → B → C。

**推论**：不能跨回调缓存"绑定 loop 的对象"——
Motor client / 模块级 `asyncio.Lock` / `asyncio.Semaphore` / httpx.AsyncClient 都会因此失效
（报错形如 `Event loop is closed` 或 `RuntimeError: ... is bound to a different event loop`）。

### 为什么用同步客户端 + "假 async"

**选择**：`core/db/client.py` 用同步 `pymongo.MongoClient`；
`core/db/` 的业务函数**保留 `async def` 签名**但内部同步调用（"假 async"）。

**理由**：

1. **Dash 的 worker 是同步线程**——一个线程处理一个回调。
   异步在这个框架里没有跨请求收益。
2. **同步客户端不绑 loop**——一次创建永久复用，简单可靠。
3. **`async def` 签名保留**——调用方的 `await mark_task_failed(...)` 零改动。
   未来切方案 C 时，只改函数体不动签名。

**代价**：失去"单回调内 `asyncio.gather` 并发 IO"的能力。
**当前无影响**——回调里都是串行 IO（`list_tasks` → `load_task_with_plan`）。

### 触发条件：什么时候切方案 C（后台常驻 loop）

**任一满足才做**：

- 需要多 worker 部署（gunicorn `--workers N`）
- 需要 UI 和任务执行跨进程共享状态
- 单进程 Dash 撑不住并发

**切换路径**：

1. `StreamManager` 从 InMemory → Redis
   （`RedisSessionStore` 已有一半基础——见 `tests/test_package/test_redis_session_store.py`）
2. 启动时创建后台常驻 loop（独立线程）
3. `commands._resume_with_command` 从 `await graph.ainvoke(...)` 改为
   `asyncio.run_coroutine_threadsafe(graph.ainvoke(...), background_loop)`
4. `db/client.py` 回到 Motor + **按 loop 缓存**（不是全局缓存）
5. `llm/client.py` 同上

**切点（改动集中处）**：
- `StreamManager` 抽象
- `db/` API 层（`tasks.py` / `plans.py` / `nodes.py` / `_helpers.py`）
- `llm/` API 层（`api.py` / `core.py`）
- `commands.py` 的 resume 通道

---

## 二、Pydantic 化现状

### 已完成

| 模型 | 位置 | 用途 |
|---|---|---|
| `IntentModel` | `core/graph/schemas.py` | 外部边界——`recognize_intent` 的 LLM 输出 |
| `NodeExecResult` | `core/graph/nodes/_executor.py` | 内部构造——`execute_single_node` 的返回 |

### 未做（可能永远不做）

| 候选 | 原因 |
|---|---|
| `TaskState` 外壳 | **绝不能换**——LangGraph 的 state 必须是 TypedDict（`MessagesState` 兼容性） |
| `NodeResult`（`state.py` 的 `node_results` 元素） | 落 state 需保持 dict——Pydantic 只在边界用，收益有限 |
| `FeedbackModel`（`Command(resume=...)` 的 dict） | 跨线程一致性——等方案 C 再评估 |

### 原则

> **边界用 Pydantic 是必须，内部构造用 Pydantic 是偏好。**
> 不追求"全类型化"——收益递减，复杂度递增。

---

## 三、日志级别

`LOG_CONSOLE_LEVEL` 曾在 `logger_setup.py` 被读取但**从未消费**——
`_init_root_logger` 用的是 `logging.DEBUG if DEBUG else logging.WARNING`。

**修复**：新增 `_resolve_console_level()`，优先级：
`LOG_CONSOLE_LEVEL > (DEBUG ? DEBUG : WARNING)`。

**当前配置**：`.env` 里 `LOG_CONSOLE_LEVEL=INFO`——
DB / LLM 等模块的 INFO 日志可见（如 `[DB] ✅ MongoDB 连接成功`）。

---

## 四、RETRY_NODE 功能的状态

`retry_node_cmd` API 已定义、`execute._resolve_target_index` 的三指针裁决
已实现、`routes.route_after_execute` 的 RETRY_NODE 分支已实现——
**但 UI 层没有入口**。

**状态**：dormant（休眠）——基础设施就绪，未来加按钮就能用。

**改名遗留**：`retry_node_id` → `retry_node_index`（2026-10-13）——
名字与语义对齐。当前链路只有 index 语义，id 兜底是防御性的。

---

## 五、清理待办

| 项 | 优先级 | 说明 |
|---|---|---|
| `abandon/` 目录 | 低 | 归档代码，不影响功能，但增加 grep 噪音 |
| `get_cancel_event` 的 config 参数残留 docstring | 低 | `commands.py` 已修 |
| `good_addons._wrap_dash_callbacks` 的 async 分支 | 已修 | 验证落地 |
| `new_task.py` 的 `mouseoutNodeData` | 已删 | 验证落地 |

---

## 六、方法论：这一路踩过的坑

### "看半截代码" 的陷阱

- **错误 1**：看到 `dash/backends/ws.py` 的 `asyncio.run` 就断定"每回调新 loop"
  —— 没看 `_flask.py`（实际用的 backend）
- **错误 2**：看到前两行 loop_id 相同，断言"每线程持久 loop"
  —— 没看完整输出（后面 loop 会变）

**教训**：**证据不足不下结论**。

### "修一半" 的陷阱

- `LOG_CONSOLE_LEVEL` 被读取但没消费
- `feedback["action"]` vs `state["user_action"]` 键名漂移
- `_wrap_dash_callbacks` 只包同步回调

**教训**：**声明与实现必须逐字对齐**——写"读取配置"就要"使用配置"，
写"支持 async 回调"就要 `await orig(...)`。

### 验证链路

任何"看起来修好了"都必须经过：

1. **单元测试**（pytest）
2. **端到端冒烟**（`task-planner` + 真实操作）
3. **负面测试**（有意送非法值，看降级路径）

**没有这三层，"修好了"只是声明**。

---

## 七、变更记录

| 日期 | 主题 | 影响 |
|---|---|---|
| 2026-10-13 | Dash loop 行为实测 + 同步客户端迁移 | DB / LLM 客户端从异步改同步 |
| 2026-10-13 | `LOG_CONSOLE_LEVEL` 修复 | 控制台 INFO 日志可见 |
| 2026-10-13 | `retry_node_id` → `retry_node_index` | 名字语义对齐 |
| 2026-10-13 | 引入 `IntentModel` / `NodeExecResult` | Pydantic 试点 |
