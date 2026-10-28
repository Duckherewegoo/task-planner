---
id: 0005-mongodb
aliases: []
tags: []
---

# ADR-0005: 采用 MongoDB 作为持久化存储

- Status: Accepted
- Date: 2026-09-06（开始编写持久化层；早于本仓库首个 commit 2026-09-27）
- Related:
  - 代码：`core/db/`（client / tasks / plans / nodes / schema）
  - 集成测试：`tests/test_package/test_mongodb.py`
  - 下游：ADR-0002（Motor 时期）、ADR-0001（同步 pymongo）
  - 注意：**本文只记存储选型**。驱动选型的演进（mongoengine → Motor → pymongo）
    是独立决策，见 ADR-0001 / ADR-0002

> **证据说明**：决策日早于本仓库首个 commit。证据为口述。

## Context

**触发**：2026-09-06 开始编写持久化层时，三个因素叠加：

1. **学习路径**：当时正在学习 MongoDB，有现成的使用经验。
   对个人项目而言，"用正在学的东西"本身就是合理动机——学习成本已经沉没。
2. **技术栈参考**：同期接触的一个 agent 项目（**不具名**）采用 MongoDB 作为存储，
   其数据形态与本项目的"任务 → 计划 → 节点"结构相似。
3. **模型契合**：任务数据是嵌套文档—— Task { question, status, plan: { nodes: [...], edges: [...] }, node_results: {...} }

`nodes` 是数组，每个节点的状态更新是"按 index 改数组里的一个元素"；
`edges` 是数组；计划整体写入、局部更新。不是频繁的关系型 join 场景。

**有意识地排除关系型**：不是"评估后认为关系型不行"，而是上述因素
让"文档型"成为默认方向，SQL 方案**未被认真纳入比较**。
如实标注这一点，避免未来误读为"SQL 已经被权衡过"。

## Decision

采用 **MongoDB** 作为持久化存储。

## Alternatives Considered

**决策时未系统评估替代方案**。以下为事后补记，非决策依据：

- **SQLite**：单文件、零运维，对单用户足够。缺点：JSON 数组的局部更新
要整列读写，或用 JSON1 扩展——比 Mongo 的 `$` 操作符笨拙。
- **PostgreSQL（JSONB）**：能力上完全够（JSONB + GIN 索引）。
当时大概率因"要起服务、要写 migration"被略过。**未留下权衡记录。**
- **MongoDB**：采纳。

## Consequences

**正面**

- 文档模型与嵌套结构同构，`plan` 整体存取无阻抗失配
- 数组元素更新用 `$` 位置操作符（`nodes.$`），贴合"改单个节点状态"的高频操作
- 无 schema migration，`TaskState` 加字段直接写
- 有官方异步（Motor）和同步（pymongo）驱动，给异步策略留了切换空间

**负面（接受的代价）**

- **早期无多文档事务**：`task` 与 `plan` 的一致性靠应用层保证。
当前单文档为主，影响有限，但"级联删除"这类操作需要小心
- **数组操作的可读性**：`$` 操作符 + 位置索引的代码不如 SQL UPDATE 直观，
`nodes.py` 的测试大部分在覆盖边界情况
- **集成测试需要真实 MongoDB**：43 个测试默认排除，CI 里靠 `MONGO_DB=*_test` 护栏

**遗留**

- **驱动换过三次**：mongoengine（ODM）→ Motor（异步）→ pymongo（同步）。
存储选型稳定，但驱动选型的反复是 ADR-0001/0002 的下游震荡
- 集成测试强制 `MONGO_DB` 以 `_test` 结尾，否则跳过——防止误清生产库
