---
id: 0004-langgraph
aliases: []
tags: []
---

# ADR-0004: 采用 LangGraph 作为编排引擎

- Status: Accepted
- Date: 约 2026-08-10（最早可查记录；早于本仓库首个 commit 2026-09-27）
- Related:
  - 代码：`core/graph/workflow.py`（图拓扑 + Checkpointer + 单例）、`core/graph/state.py`
  - 设计约束：`docs/ARCH_FUTURE.md` §二（TaskState 必须是 TypedDict）
  - 同期决策：ADR-0003（UI 框架选 Gradio → Dash，2026-08-15）
  - 下游：ADR-0001、ADR-0002（异步策略围绕 LangGraph 的流式执行展开）

> **证据说明**：决策日早于本仓库首个 commit（`f16172e`，2026-09-27）。
> 2026-08 上旬是项目雏形期，多个技术栈选型（编排引擎、UI 框架）在此阶段并行决定，
> 代码史未进入本仓库，证据为口述 + 旧版 README。

## Context

**项目阶段**：雏形期，正在试多个原型。编排引擎与 UI 框架是两个并行的选型问题。

任务规划的核心流程是**多步状态机**：intent → plan → refine → save → render → execute，
每步可能改写共享状态（`TaskState`），需要条件路由（是否需要规划、是否重试、是否取消）、
需要流式中间结果（前端实时更新流程图）、需要断点续跑（历史任务"继续执行"）。

手写这套东西要自己解决：状态传递、条件边、并发节点调度、流式产出、
checkpoint 持久化、中断/恢复语义。项目是单人开发，这些不该从零造。

## Decision

采用 **LangGraph**（`langgraph>=1.2.11`）作为编排引擎。
图拓扑在 `workflow.py` 中声明，状态用 TypedDict，checkpoint 后端可配
（memory / sqlite / postgres）。

## Alternatives Considered

评估状态如实标注——**决策当时的实际比较范围可能小于此列表**：

- **A. 手写状态机**：可行，但要自造 checkpoint、流式、中断恢复。
  推断当时认为成本高于引入框架。**未留下书面权衡记录。**
- **B. 纯 prompt chain（无图）**：无法表达条件路由和 DAG 依赖，
  与"动态 DAG 编排"这一核心卖点直接冲突。**基本可判定为不适合。**
- **C. 其他 agent 框架（AutoGen / CrewAI / LlamaIndex）**：
  **未评估**。事后看，LangGraph 的"低层图原语 + 显式状态"更贴合本项目
  "自己控制每个节点"的需求，而 CrewAI 等偏高层封装。但这是事后判断，
  不是决策依据。
- **D. LangGraph**：采纳。

## Consequences

**正面**

- 图拓扑声明式：节点函数 + 条件边，`routes.py` 的路由逻辑是纯计算，好测
- checkpoint 内置：历史任务"断点续传"直接复用，无需自造
- 流式原生：`graph.astream(...)` 产出事件，喂给 `TaskStreamState`
- 状态传递显式：`TaskState` 的字段读写契约清晰，多角色协作（黑板模型）自然成立

**负面（接受的代价）**

- **框架级锁定，迁移成本极高**：图拓扑、checkpoint 格式、流式事件结构都绑在 LangGraph 上
- **`TaskState` 必须是 TypedDict**：LangGraph 的 state 与 `MessagesState` 兼容性要求，
  不能换成 Pydantic 外壳（见 `ARCH_FUTURE.md` §二）。这限制了状态层的类型化程度
- **调试黑盒**：图执行内部调度不可见，出问题要靠 `astream` 事件和日志反推
- **依赖 LangChain 生态**：版本升级有连带风险

**遗留**

- `retry_node_cmd` 完整链路已实现但无 UI 入口，长期 dormant（见 L3 决策日志）
