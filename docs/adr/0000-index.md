---
id: 0000-index
aliases: []
tags: []
---

# 架构决策记录（ADR）索引

> 本目录记录**关键架构决策的"为什么"**——决策时刻的背景、选了什么、放弃了什么、
> 什么条件下要重审。详细的技术现状与切换路径见 `docs/ARCH_FUTURE.md` 与
> `docs/DESIGN_task_context.md`，ADR 不重复它们。
>
> **约定**：
> - 一个决策一篇，文件名 `NNNN-名词短语.md`
> - 被推翻的决策不删，标 `Superseded by`，形成决策链
> - 小决策不独立成篇，进 `0008-small-decisions-log.md`（活文档）
> - 新增编号递增，不改已有条目

## 决策列表

| 编号 | 标题 | 状态 | 决策日 | 一句话 |
|---|---|---|---|---|
| [0001](0001-async-boundary.md) | 异步边界分层 | Accepted | 2026-10-13 实测 / 10-14 落地 | 后台任务常驻 loop、DB 同步 pymongo、LLM 异步、UI 假 async |
| [0002](0002-full-chain-async.md) | 转向全链路异步 | **Superseded by 0001** | 2026-09-28 / 推翻 2026-10-13 | 为"战未来"全面 async 化，被 Dash loop 实测证伪 |
| [0003](0003-gradio-to-dash.md) | UI 框架 Gradio → Dash | Accepted | 2026-08-15 | pyvis 交互需求 + JS 注入不安全，Gradio 不适配 |
| [0004](0004-langgraph.md) | 采用 LangGraph 编排 | Accepted | 约 2026-08-10 | 多步状态机 + 流式 + checkpoint，不自造轮子 |
| [0005](0005-mongodb.md) | 采用 MongoDB 存储 | Accepted | 2026-09-06 | 学习路径 + 技术栈参考 + 嵌套文档契合 |
| [0006](0006-cog-config.md) | 配置系统 cog | Accepted | 2026-10-01 | scope 分级 + 密钥强制 from_env 不落盘 |
| [0007](0007-python-314.md) | Python 3.14+ 下限 | Accepted | 2026-10-07 | 个人项目用新特性，代价是用户群窄 |
| [0008](0008-small-decisions-log.md) | 小决策日志 | Living | 2026-10-28 汇编 | 7 条够不上独立成篇的提醒 |

## 决策链

三篇构成一条因果链，读的时候建议按这个顺序：
ADR-0003（选 Dash，2026-08-15）
│ Dash WSGI 的 event loop 会变（当时未知）
▼
ADR-0002（为战未来转全链路异步，2026-09-28）
│ 实测 loop A→B→C，异步客户端假设破产
▼
ADR-0001（异步边界分层，2026-10-13/14）


**如果不选 Dash，0002 和 0001 都不必存在。** 这是 ADR-0003 最重要的下游后果。

## 按主题归类

- **框架/引擎选型**：0003（UI）、0004（编排）、0005（存储）
- **异步与并发**：0001、0002（一组对立决策）
- **工程基础设施**：0006（配置）、0007（语言版本）
- **零散约定**：0008（活文档）

## 状态说明

| 状态 | 含义 |
|---|---|
| Accepted | 当前生效 |
| Superseded by XXXX | 已被取代，保留供追溯 |
| Deprecated | 不再适用，但未被新决策明确取代 |
| Proposed | 提议中，未落地 |
| Living | 活文档，持续追加（仅 0008） |
