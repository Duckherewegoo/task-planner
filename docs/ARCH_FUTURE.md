---
id: ARCH_FUTURE
aliases: []
tags: []
---

# 架构演进路径

> **项目定位**：个人学习研究项目，非生产级。目标是把 Dash + FastAPI + LangGraph + Motor 的全异步架构从头到尾摸透，顺便展示技术深度。
>
> **可用性**：所有功能在个人电脑上验证可用。架构设计上尽可能往生产级靠拢，但不保证生产环境不崩——没有多 worker、没有高可用、没有灾备，这是刻意的选择，不是遗漏。
>
> 本文档记录"还能往哪走"——不是生产环境的 TODO 清单，而是有意思的技术挑战和值得验证的架构想法。
>
> 当前架构基线：FastAPI backend + Motor 异步 + 双通道取消（详见 ADR-0009）
>
> 最近更新：2026-11-26

---

## 一、已落地的架构基线

| 层 | 技术 | 关键决策 |
|---|---|---|
| UI | Dash 4.4.1（backend="fastapi"） | 从 Flask 硬切到 FastAPI，回调直接跑在 uvicorn 持久 loop 上，解决了 Flask backend 下 loop 漂移的根本问题 |
| 任务 | LangGraph | `asyncio.create_task` 后台流式执行，支持 interrupt/resume；双通道取消把最长等待从 60s 降到接近 0 |
| DB | Motor（AsyncIOMotorClient） | 懒初始化绑定 uvicorn loop，shutdown 分步兜底（清理循环 → state_cleaner → LLM 连接池 → DB），顺序固定 |
| 会话存储 | InMemory（默认）/ Redis（可选） | `RedisSessionStore` 全异步实现，原生 TTL，含完整的语义差异文档——调用方切换零改动 |

> 完整决策过程和踩坑细节见 **ADR-0009**。

---

## 二、可以进一步探索的方向

### 2.1 多进程 / 分布式状态

**现状**：单进程 uvicorn，状态全在内存。个人电脑上够用，但这是离"生产级"最远的一块。

**挑战**：如果开多个 worker，`TaskStreamState` / `_aborts` / `_resume_locks` 就看不见了。经典的分布式状态问题。

**可以怎么研究**：
- 启用 `RedisSessionStore`（已实现），但 `asyncio.Event` / `asyncio.Task` 不能 pickle——怎么把"可序列化状态"和"运行时原语"分离？这是一个设计模式的好练习。
- sticky session（nginx ip_hash）—— 简单粗暴但有效
- 更进一步：Redis Pub/Sub 做跨 worker 取消信号广播

**诚实边界**：目前没有多 worker 需求，以上只是"我知道怎么搞，但还没必要搞"。

### 2.2 取消的彻底性：同步 LLM 调用怎么杀

**现状**：双通道取消已落地，但 `abort_worker` 发 `CancelledError` 时，如果 LLM 客户端是同步的（当前 `openai` 是同步客户端），HTTP 调用不会被中断。

**挑战**：如何让一个同步阻塞调用响应取消？Python 并发的经典难题。

**可以怎么研究**：
- 换 `openai.AsyncOpenAI` —— 但整个 LLM 客户端的抽象要重构成异步
- `httpx.AsyncClient` + 超时兜底
- 更野的：用 `signal` / `concurrent.futures` 包装同步调用
- 研究 `anyio` 的 `move_on_after` 能否解决这个问题

**诚实边界**：当前 LLM 调用是同步的，取消后最长等一个 HTTP 请求超时。这是已知的妥协。

### 2.3 standalone 模式的 lifecycle 管理

**现状**：`cleanup_stale_sessions` 等 lifespan 钩子只在 FastAPI 注册。纯 Dash/Werkzeug 入口不走这条链路，会话可能泄漏。

**挑战**：一个项目同时支持两种 backend 模式，各自的 lifecycle 怎么管理？"同一套代码适配不同运行时"的好练习。

**诚实边界**：standalone 模式是个人电脑上的备选启动方式，不是生产入口，泄漏风险可接受。

### 2.4 RedisSessionStore 的语义差异

**现状**：已实现，但与 InMemory 版有三处行为差异（重复 set 重置 TTL / cleanup_stale 是 no-op / 不能存 asyncio 原语）。

**挑战**：两种存储后端行为不一致时，怎么设计接口才能"调用方零改动"？`redis_session_store.py` 的 docstring 已经写了 200 行差异说明——这本身就是一种设计展示。

---

## 三、Pydantic 化边界

> "边界用 Pydantic 是必须，内部构造用 Pydantic 是偏好。"

| 模型 | 状态 | 说明 |
|---|---|---|
| `IntentModel` | ✅ 已落地 | LLM 输出边界——外部数据必须校验 |
| `NodeExecResult` | ✅ 已落地 | 内部构造——类型安全 |
| `TaskState` 外壳 | ❌ 永不 | LangGraph 要求 TypedDict，框架约束优先于类型偏好 |
| `FeedbackModel` | 🔮 待定 | 等哪天想玩"前后端类型共享"时再评估 |

这一节展示的是：**我知道什么时候该用 Pydantic，什么时候不该**——不是无脑加类型。

---

## 四、RETRY_NODE：基础设施就绪，等一个 UI

**状态**：dormant（休眠）——后端逻辑完整，前端没接入口。

**展示价值**：说明架构的前后端解耦做得好——后端功能可以独立存在，前端随时可以"插上就用"。

---

## 五、踩坑记录

> 作为学习项目，踩坑不是失败，是最有价值的经验。

### "看半截代码" 的教训

- 看到 `dash/backends/ws.py` 的 `asyncio.run` 就断定"每回调新 loop" —— 没看 `_flask.py`（实际用的 backend）
- 看到前两行 loop_id 相同就断言"持久 loop" —— 没看完整输出（后面 loop 会变）

**收获**：证据不足不下结论——调试异步代码的基本功。

### "修一半" 的教训

- `LOG_CONSOLE_LEVEL` 被读取但没消费
- `feedback["action"]` vs `state["user_action"]` 键名漂移
- 硬取消方案设计好了但代码没写进去（2026-11 已落地）

**收获**：声明与实现必须逐字对齐——架构设计不是画饼，是每一行代码都要落地。

### 验证链路

1. 单元测试（pytest）
2. 端到端冒烟（真实操作）
3. 负面测试（非法值降级路径）

**收获**：没有这三层，"修好了"只是自我安慰。

---

## 六、变更记录

| 日期 | 主题 | 说明 |
|---|---|---|
| 2026-11-26 | 文档重新定位为"未来探索" | 从现状描述转为架构演进方向 |
| 2026-11-26 | FastAPI backend 迁移落地 | 见 ADR-0009，全链路异步打通 |
| 2026-11-26 | 双通道取消 + _resume_locks 清理 | 取消机制的设计深度展示 |
| 2026-10-13 | Dash loop 行为实测 + 同步客户端迁移 | 历史探索（已过时，保留作为学习记录） |
