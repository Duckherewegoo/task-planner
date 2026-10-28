---
id: 0008-small-decisions-log
aliases: []
tags: []
---

# ADR-0008: 小决策日志

- Status: Living（活文档，持续追加）
- Date: 2026-10-28（首次汇编）
- Related:
  - 详细原则：`docs/ARCH_FUTURE.md`（Pydantic §二、RETRY_NODE §四）
  - 目录：`docs/adr/README.md`（索引）
  - 说明：本文件不替代独立 ADR。单个决策若后来影响面扩大，从中拆出独立成篇，
    并在本文条目上标 `Promoted to ADR-XXXX`

> **用途**：记录"值得留个提醒、但够不上独立成篇"的小决策。
> **格式**：每条含 `编号 / 日期 / 决策 / 为什么 / 重审条件（可选）`。
> **维护**：只追加不改写；被推翻的条目标 `Superseded by L3-XX`，保留原文。

---

## L3-01 保留 `abandon/` 目录（不删、不 import、不打包）

- 日期：约 2026-10-01 ~ 10-02（模块化重构期成型，精确日期不可考）
- 决策：`src/task_planner/abandon/`、`tests/abandon/` 保留在仓库，但
  `pyproject.toml` 的 `packages.find` 排除、`ruff`/`pyright` 排除、`pytest` 的
  `norecursedirs` 排除、`src/` 下代码禁止 import。
- 为什么：三个用途——① 出奇怪 bug 时 diff 出"是否重构引入"；
  ② `scripts/check_config_drift.py --old` 用它做基线；
  ③ 旧版 README 等历史证据随仓库保留。
- 约束：`.gitignore` 不要加 `abandon/`；不要迁移 changelog；不要 import。
- 重审条件：仓库体积成为问题，或 `check_config_drift.py` 废弃。

## L3-02 per-module changelog（集中到 `CHANGELOG.md`）

- 日期：2026-10-14（commit `6f2fc99`）
- 决策：各模块历史变更集中到根目录 `CHANGELOG.md`；源文件顶部只留
  "模块职责 + 最新一条变更 + 指针"。
- 为什么：源文件顶部曾积到 40+ 行历史段，挤压正文可读性。
  集中后源文件压到 5-20 行，历史仍可查（`CHANGELOG.md` + `git log`）。
- 例外：`good_addons.py` 是"可复制到任何项目的 drop-in 库"，
  changelog 保留在文件内——离开本项目后 `CHANGELOG.md` 不跟随。
- 重审条件：`CHANGELOG.md` 大到难以定位，考虑按包拆分。

## L3-03 Pydantic 只在边界用

- 日期：2026-10-13（commit `7f391e4`，值对象试点）
- 决策：**边界用 Pydantic 是必须，内部构造用 Pydantic 是偏好。**
  已落地 `IntentModel`（LLM 输出边界）、`NodeExecResult`（执行器返回值）；
  `TaskState` 外壳与落 state 的 `NodeResult` 保持 TypedDict / dict。
- 为什么：`TaskState` 必须是 TypedDict（LangGraph 的 `MessagesState` 兼容性硬约束）；
  Pydantic 在内部构造点收益递减、复杂度递增。
- 重审条件：LangGraph 支持 Pydantic state，或内部构造点出现反复的类型漂移 bug。

## L3-04 `retry_node_cmd` 保持 dormant（有链路无入口）

- 日期：2026-10-14（commit `8010290` 明确标注）
- 决策：重试链路的 API、三指针裁决（retry > resume > current）、
  `routes.route_after_execute` 的 RETRY_NODE 分支全部保留，
  **UI 层不加按钮**。
- 为什么：基础设施已实现且被测试覆盖，加 UI 入口是独立的产品决策。
  保留代码的成本（维护）低于删了再写的成本。
- 重审条件：用户需要"重试失败节点"功能，或代码腐烂到测试维护成本超过收益。
- 注意：`retry_node_id` → `retry_node_index` 已改名（`7f391e4`），
  名字与语义对齐——装的是 index，不是 id。

## L3-05 UI 轮询间隔 500ms → 1000ms

- 日期：2026-10-14（commit `a9d2118`）
- 决策：`dcc.Interval` 的 `interval` 从 500 改为 1000。
- 为什么：减少前端轮询压力；1s 延迟对任务进度展示可接受。
- 重审条件：用户反馈进度更新明显滞后，或改为推送模型（见 ADR-0001 的触发条件 3）。

## L3-06 Dash 端口 7860 → 8050

- 日期：2026-10 上旬（精确日期不可考；随 Gradio 残留清理，见 commit `3055b7b`）
- 决策：默认端口改为 8050（Dash 惯例），弃用 7860。
- 为什么：7860 是 Gradio 的默认端口，迁移后长期沿用。
  现 README 专门强调"7860 是 Gradio 的，本项目已弃用"——这个提醒本身是迁移遗留。
- 交叉引用：ADR-0003 的"遗留物"一节已记录此条。
- 重审条件：无（纯清理）。

## L3-07 LLM 环境变量命名：通用兼容 ↔ 具体供应商的摇摆

- 日期：2026-10 前后（演进，无单点 commit）
- 决策：当前使用 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL`。
- 演进史（如实记录，非"一路向前"）：
  - Gradio 时代（very_old README）：`DASHSCOPE_API_KEY` / `DASHSCOPE_MODEL`
  - Dash 迁移期（old README）：`OPENAI_API_KEY` / `OPENAI_BASE_URL`（通用兼容命名）
  - 当前（README）：`DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL`（回到具体供应商）
- 为什么摇摆：`OPENAI_*` 命名表达"OpenAI 兼容接口"的通用性，
  但本项目实际主用 DashScope 兼容模式；`DASHSCOPE_*` 更直白，
  代价是若未来换供应商要改名。
- 重审条件：接入第二个 LLM 供应商，或需要"一个变量名同时适配多家"时，
  考虑改为 `LLM_API_KEY` + provider 子配置。

---

## 追加约定

1. **新增条目**：编号递增（`L3-08`…），不改已有条目。
2. **推翻旧条目**：旧条目标 `Superseded by L3-XX`，正文保留，新条目写清推翻理由。
3. **升级为独立 ADR**：条目标 `Promoted to ADR-XXXX`，正文保留一句指针，
   详细内容移入独立 ADR。
4. **够不上"提醒"的**（如纯格式化、拼写修正）不进来——那属于 `git log`。
