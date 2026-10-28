---
id: 0003-gradio-to-dash
aliases: []
tags: []
---

# ADR-0003: UI 框架从 Gradio 迁移到 Dash + Cytoscape

- Status: Accepted
- Date: 决策 2026-08-15 19:45（口述）/ 落地 ~2026-09 上旬 / 彻底清残留 2026-10-10
- Related:
  - 决策前证据：旧版 README（Gradio + Vis.js 形态）——**已在仓库My-AI-Journey归档，不在本仓库**
  - 迁移中证据：旧版 README（Dash + Cytoscape 形态，端口仍为 7860）——同上
  - 关键 commit：`ce31979`（2026-09-28，Dash 4.x 兼容修复）、
    `e22fc02`（2026-10-10，彻底清除 Gradio 痕迹）、
    `3055b7b`（2026-10-10，pyproject/.env 残留清理）
  - 下游影响：ADR-0001、ADR-0002（Dash WSGI 的 event loop 问题）

> **证据说明**：本决策日（2026-08-15）**早于本仓库首个 commit**（`f16172e`，2026-09-27）。
> 迁移前的代码史未进入本仓库，证据来自口述 + 两份旧版 README。

## Context

**产品需求**：核心卖点是**可交互的 DAG 流程图**——节点悬停/点击查看详情、
节点状态（待处理/进行中/成功/失败/超时）实时着色、流式执行进度反映到图上。
流程图不是配图，是主交互界面。

**当时的实现形态**（见旧版 README「AI 任务规划看板 v3.0」）：

Gradio 6.x（外壳 + 主题）
├── 事件链：生成 / 加载 / 导出
├── Vis.js 交互式流程图（前端 JS）
├── FastAPI 路由 /api/node-status（供前端 JS fetch 调用）
└── matplotlib 服务端生成 PNG（状态着色）


即 **Gradio 只是外壳，真正的交互靠旁挂的 FastAPI 路由 + 运行时注入的自定义 JS**。

**问题**：三个症状指向同一个根因。

1. **Gradio 不支持高度自定义**：流程图必须是 Vis.js 渲染的原生 DOM，
   Gradio 组件体系里没有对应物，只能塞进 `gr.HTML` 再由外部 JS 接管。
2. **自定义 JS 只能运行时注入**：Gradio 没有官方的静态资源/脚本挂载机制，
   注入时机、作用域、与 Gradio 自身生命周期的协调都不可控。
   用户判断：**这种方式不安全**（非受控执行环境、绕过框架的渲染边界）。
3. **被迫旁挂 FastAPI**：前端 JS 需要更新节点状态，Gradio 的回调是 Python 侧的，
   拿不到前端 JS 的直接调用入口，只好另起一个 FastAPI 提供 `/api/node-status`。
   **一个 UI 框架要配一个额外 Web 服务才能完成主交互**，这是框架不适配的硬证据。

**约束**：个人项目，单人开发，前后端两套语言栈的成本不可接受；
需要 Python 侧能直接控制前端交互。

## Decision

**放弃 Gradio，改用 Dash（Flask WSGI）+ dash-cytoscape。**

- UI 框架：Dash 4.x
- 流程图：`dash-cytoscape`（Cytoscape.js 的官方 Python 封装）
- 交互：Dash 回调（Python 侧）+ `assets/` 静态资源挂载自定义 JS/CSS
- 执行进度：`dcc.Interval` 轮询（初版 500ms，后调 1000ms，见 `a9d2118`）

**核心判断**：**主交互界面必须是框架的一等公民，而不是"塞进去的外部 JS"。**
Dash + dash-cytoscape 让流程图成为声明式组件，回调直接读写节点数据，
不再需要旁挂 FastAPI，也不再需要运行时注入脚本。

## Alternatives Considered

**A. 留在 Gradio + JS 注入 + 旁挂 FastAPI** —— 否决
- 上面 Context 的三条症状
- 用户明确判断"不安全"，这是否决的决定性理由
- 架构上"UI 框架 + 额外 Web 服务"的组合，维护成本随功能增长

**B. 纯前端（React / Vue）+ FastAPI 后端** —— 未在决策时评估
- 能满足全部交互需求，但个人项目要维护两套语言栈
- 记录为"未评估"，不是"评估后否决"——避免未来误读为已经权衡过

**C. Streamlit** —— 未在决策时评估
- 与 Gradio 同属"快速原型"类框架，大概率有同类限制
- 同样记录为"未评估"

**D. Dash + Cytoscape** —— 采纳
- `dash-cytoscape` 是 Cytoscape.js 的官方封装，流程图成为声明式组件
- Dash 回调模型：Python 侧直接读写组件属性，无需旁挂服务
- `assets/` 目录是官方支持的静态资源挂载点，自定义 JS 受控
- 同为 Python 生态，无跨语言成本

## Consequences

**正面**

- 流程图成为一等公民：节点数据 → Cytoscape 元素 → 回调读写，链路单一直达
- 移除旁挂 FastAPI，架构收敛为单一 Dash 应用
- `assets/` 提供官方的 JS/CSS 挂载点，"运行时注入"的不安全面消失
- 回调模型让"节点操作 → 状态更新 → 图重绘"在一个 Python 函数里闭环

**负面（接受的代价）**

- **引入了 ADR-0001/0002 的整条问题链**。Dash 4.x 的 Flask WSGI backend
  用 `asgiref.async_to_sync` 驱动 async 回调，同 thread 内 event loop 会变化
  （A→B→C，2026-10-13 实测）。**这是选择 Dash 的直接下游代价**——
  Gradio 时代不存在这个问题（因为根本不走 async 回调）。
  因果链：ADR-0003（选 Dash）→ ADR-0002（为战未来转全异步）→ ADR-0001（异步边界分层）。
  **如果不选 Dash，后两篇 ADR 都不必存在。** 这是本 ADR 最重要的后果。
- 轮询模型而非推送：`dcc.Interval` 定期拉取，有固定延迟和空转开销。
  初版 500ms，后调 1000ms 减压（`a9d2118`）。
- Dash 4.x 迁移期有兼容成本：`html.Style` 迁移到 `assets/custom.css`、
  前端常量补齐、CSP 死 import 清理等（`ce31979`，2026-09-28）。
- **迁移是"决策快、清理慢"**：决策在 8-15，但 Gradio 的依赖残留
  （pyproject 可选组、`.env` 变量、旧启动入口）直到 **2026-10-10**
  才彻底清除（`e22fc02` / `3055b7b`）——**近两个月的尾巴**。
  教训：框架迁移的"完成"应以"依赖图里搜不到旧框架"为准，不以"新框架能跑"为准。

**遗留物**

- **端口 7860 是从 Gradio 时代继承的**。迁移后长期沿用（旧版 README 仍写
  `DASH_PORT=7860`），后改为 8050。现 README 专门强调"7860 是 Gradio 的默认端口，
  本项目已弃用，不要混用"——这个提醒本身就是迁移遗留的化石。
- `planner-very_old_readme.md`（Gradio 形态）与 `planner-old_readme.md`
  （Dash 迁移中形态）是本 ADR 的一手证据，建议归档到 `docs/history/` 随仓库保留。

## 备注：为什么这篇的 Alternatives 有两项"未评估"

个人项目的 ADR 最容易犯的错，是事后把"没考虑过的选项"写成"考虑过但否决了"，
制造出并不存在的权衡过程。本 ADR 如实标注：决策当时只确认了"Gradio 不行"
和"Dash 能满足"，未系统评估 Streamlit 或前后端分离。
这不影响决策的正确性（Dash 至今仍适用），但影响未来复盘时的信息可信度——
**未来若重新评估 UI 框架，不要以为 B/C 已经被认真考虑过。**
