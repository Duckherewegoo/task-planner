---
id: 0001-async-boundary
aliases: []
tags: []
---

# ADR-0001: 异步边界分层——任务常驻 loop / DB 同步 / LLM 异步 / UI 假 async

- Status: Accepted
- Date: 决策 2026-10-13（实测证伪当日）/ 落地 2026-10-14（async_runtime 合入）
- Supersedes: ADR-0002（2026-09-28 转向全链路异步）
- Related:
  - 详细展开：`docs/ARCH_FUTURE.md` §一
  - 落地 commit：`7a2c736`（DB 迁移同步 pymongo）、`b642da9`（引入 async_runtime）
  - 代码：`infrastructure/async_runtime.py`、`core/db/client.py`、`infrastructure/llm/client.py`

## Context

**背景**：本 ADR 取代 ADR-0002。2026-09-28 项目按 ADR-0002 转向全链路异步：
DB 用 Motor、LLM 用 AsyncOpenAI、后台任务用 `asyncio.create_task`。
Dash 4.4.1 的 Flask WSGI backend 通过 `asgiref.async_to_sync` 驱动 async 回调，
与上述"绑定 loop 的异步客户端"假设产生冲突。

**问题**（2026-10-13 实测）：

- 同一 thread 内连续触发同一回调，`id(asyncio.get_running_loop())` 序列 = **A → B → C**。
  即 **event loop 不可跨回调依赖**。
- 后果一：Motor 的 `AsyncIOMotorClient` 绑定 loop，跨 loop 复用抛
  `Event loop is closed` / `... is bound to a different event loop`。
- 后果二：原实现在回调 loop 上 `create_task` 的 worker / cleaner，
  **回调返回后立即被取消**（实测 Cleaner 与 Agent 任务同秒内先后被取消）。
- 后果三：`asyncio.Lock` / `asyncio.Semaphore` / `httpx.AsyncClient` 等
  凡绑定 loop 的对象，全部失效。

**约束**：

- Dash 的 worker 是**同步线程**——一个线程处理一个回调，异步在此框架内没有跨请求收益。
- 项目是个人项目、单进程部署，暂无多 worker 需求。
- 但 LLM 调用是真正的长耗时 IO（最长 `LLM_NODE_TIMEOUT`=60s），
  异步取消/超时控制仍有价值。

**旧决策为什么失败**：两层原因叠加。

1. **框架层**：Dash WSGI 的 loop 生命周期与"绑定 loop 的异步客户端"假设不兼容。
   这是事实约束，不是选型品味问题。
2. **实现层**：ADR-0002 的执行不彻底——DB 层一度三种写法并存（原生 async 函数、
   纯同步函数、以及"同步函数包 `async def` + `asyncio.wait` 强行异步"）。
   这种混乱加剧了 loop 问题的排查难度：报错位置与根因位置不在同一处。
   详见 ADR-0002 的 Consequences。

## Decision

采用**异步边界分层**策略，按"是否跨回调存活"决定每层的同步/异步选型：

| 层 | 选型 | 理由 |
|---|---|---|
| UI 回调 | 声明 `async def`，内部同步调用 | 兼容 Dash WSGI；签名保留便于未来切 ASGI |
| 后台任务（worker / cleaner） | `infrastructure/async_runtime.py` 进程级常驻 loop（独立 daemon 线程） | 必须跨回调存活 |
| DB 客户端 | **同步 `pymongo.MongoClient`** | 不绑 loop，一次创建永久复用；worker 是同步线程 |
| LLM 客户端 | **`AsyncOpenAI`** | 真异步长耗时 IO；跑在常驻 loop 上 |
| DB 业务函数 | 保留 `async def` 签名，内部同步调用（"假 async"） | 调用方 `await` 零改动，未来切回异步只改函数体 |

**核心原则**：**绑定 loop 的对象不能跨回调缓存；不绑 loop 的对象可以永久复用。**
据此，DB 走同步（不绑 loop），LLM 走异步（跑在唯一常驻 loop 上）。

## Alternatives Considered

**A. 保持全链路异步（Motor + 回调内 create_task）** —— 否决
- 就是旧方案，已被实测证伪（loop A→B→C）
- 每次回调重建 Motor client → 连接池爆炸
- 后台任务被静默取消

**B. 全同步（连 LLM 也用同步 openai）** —— 否决
- LLM 调用最长 60s，同步阻塞会卡住 Dash worker
- 取消信号（`cancel_event`）只能在调用返回后检查，用户点取消要等满超时

**C. 立即切 ASGI 后端（FastAPI 或 dash[async] 真异步）** —— 暂缓，非否决
- 能一次性解决 loop 问题，但迁移成本高（UI 回调、部署、Docker 全要动）
- 当前单进程单用户场景撑得住，无迫切需求
- 切换路径已写入 `ARCH_FUTURE.md` §一「触发条件」，见下

**D. 同步 pymongo + 常驻 loop + 异步 LLM（选中）** —— 采纳
- 三层的选型分别匹配各自的生命周期与 IO 特性
- 改动集中、调用方签名不变、测试全绿（183 passed）

## Consequences

**正面**

- 后台任务不再被静默取消；实测任务完整执行 50s，5 个 stream event 全部收到，DB 写入成功
- DB 连接池稳定，无跨 loop 重建
- LLM 调用仍可被 `cancel_event` 在流式分支打断（非流式分支除外，见负面）
- 所有调用方签名不变——`async def` + `await` 保留，未来切方案 C 只改函数体

**负面（接受的代价）**

- **失去单回调内 `asyncio.gather` 并发 IO 的能力**。当前回调都是串行 IO
  （`list_tasks` → `load_task_with_plan`），无实际影响；未来若需要并发读多个集合，
  要么走常驻 loop，要么接受串行。
- **"假 async" 有认知负担**：`core/db/*.py` 的函数是 `async def` 但内部同步，
  新人（或半年后的自己）容易误以为可以 `gather`。缓解：`ARCH_FUTURE.md` §一已注明。
- **非流式 LLM 调用不可打断**：完整超时（60s）内点取消无效果。缓解见
  `DESIGN_task_context.md` §4.1，硬取消待方向 C。
- **常驻 loop 是进程级单例**：多 worker 部署时每个 worker 各有一个，
  跨进程任务状态共享仍需方案 C。

**触发重审的条件（任一满足即重看本 ADR）**

1. 需要多 worker 部署（`gunicorn --workers N` / `uvicorn --workers N`）
2. 需要 UI 与任务执行跨进程共享状态
3. 单进程 Dash 撑不住并发
4. 需要单回调内并发多个 DB / LLM 调用，且串行不可接受

切换路径（四阶段）见 `ARCH_FUTURE.md` §一「触发条件：什么时候切方案 C」，
以及 `DESIGN_task_context.md` §8。

## 备注：为什么 async_runtime 不单独成篇

`async_runtime.py`（常驻 loop）是本文 Decision 表格中"后台任务"一行的**落地手段**，
不是独立决策。单独成篇会产生两篇互相引用的 ADR，反而增加追溯成本。
未来若常驻 loop 本身需要被替换（如换消息队列），届时再单独开 ADR 并 supersede 本文的相关行。
