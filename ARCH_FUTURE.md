# 架构现状与演进路径

## 现状
- UI 层：Dash（WSGI 同步框架）
- 任务层：LangGraph 异步节点，但运行在 Dash 的 async 回调里
- 执行模型：每回调 asyncio.run()，跑完即毁 loop
- DB / LLM 客户端：同步（方案 A）

## 为什么 UI 和任务不拆
- 当前部署是单进程
- 无多 worker 需求
- 流式响应靠 StreamManager（内存）—— 没有跨进程需求

## 未来切方案 C 的触发条件
- 需要多 worker 部署
- 需要 UI 和任务执行解耦
- 需要任务执行跨进程共享

## 切方案 C 的路径
1. StreamManager 的 InMemory → Redis 实现（已有一半）
2. 后台常驻 loop + 独立线程
3. 所有 async API 从"假 async 同步实现"改成"run_coroutine_threadsafe 投递"
4. DB / LLM 客户端回到异步（保留当前异步版本作参考）

## 关键切点
- `StreamManager` 抽象
- `db/` API 层
- `llm/` API 层
- `commands.py` 的 resume 通道
