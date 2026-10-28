---
id: 0006-cog-soape
aliases: []
tags: []
---

# ADR-0006: 配置系统 cog——scope 分级 + 密钥强制走环境变量

- Status: Accepted
- Date: 2026-10-01（commit `1a878cb`，全项目模块化重构同期）
- Related:
  - 代码：`infrastructure/cog/`（7 个内置 section）
  - 配置：`config/schema.yaml`（开发者声明）/ `config/user.yaml`（用户覆盖）/ `.env`（密钥）
  - 后续补丁：commit `de58e6a`（2026-10-16，`_safe_dev_get` 兜底，import 不再依赖 .env）
  - 前端：`config_ui/`（从 `cog.hub.snapshot()` 自动生成表单）

## Context

重构前配置是**单文件大宗 `config.py`**（现存 `abandon/config_old.py`），问题：

1. **密钥与普通配置混放**：API Key 有落盘到代码/YAML 的风险
2. **无法区分"谁能改"**：开发者声明的默认值、用户偏好、运维注入的环境变量
   三者没有边界
3. **配置工具无从生成**：字段散落在代码里，没有机器可读的声明

## Decision

引入 **`cog` 配置包**，三件事：

1. **scope 分级**：每个配置项声明 `scope`（dev / user / secret 等），
   决定它出现在 `schema.yaml`（默认值）还是可被 `user.yaml` 覆盖
2. **密钥强制 `from_env`**：`secret: true` 的字段必须在 `schema.yaml` 里
   声明 `from_env: true`，运行时只从环境变量读，**永不落盘**
3. **读取顺序**：环境变量（仅 `from_env=true`）→ `user.yaml`（仅 `scope=user`）
   → `schema.yaml`（默认值）

配套：`config_ui` 从 `hub.snapshot()` 自动生成表单，新增字段无需改工具代码。

## Alternatives Considered

- **A. 保持单文件 config.py + 约定俗成**：否决。密钥落盘风险无强制手段，
  配置工具无法自动生成
- **B. pydantic-settings**：能力上够（BaseSettings + env 前缀）。
  **未评估**。事后看，它没有"scope 分级"和"配置工具自动生成表单"的原生支持，
  cog 的自研部分主要在这两块
- **C. dynaconf / environs**：**未评估**
- **D. 自研 cog**：采纳。代价是维护成本，收益是 scope 语义和 snapshot 接口完全可控

## Consequences

**正面**

- **密钥不落盘有强制手段**：`from_env: true` 是护栏，不是注释
- 配置工具零维护：新增字段只改 `schema.yaml`，表单自动出现
- `hub.reload()` 支持热重载，主应用内嵌抽屉改完即生效
- 7 个 section 按域拆分（llm / mongo / render / runtime / security / dashscope / base），
  比单文件好定位

**负面（接受的代价）**

- **自研维护成本**：loader / registry / guard / session / store 五六个模块，
  出问题要自己修
- **scope 规则有学习曲线**：新加一个配置项要先想清楚放哪个 scope
- **import 时机敏感**：`cog/__init__.py` 模块级读取配置，曾导致"import 就必须有 .env"，
  靠 `_safe_dev_get` 兜底补丁（`de58e6a`）缓解——这是设计遗留的脆弱点

**遗留**

- `_safe_dev_get` 让"未配置字段的值从抛异常改为 None"，
  未来消费方需自行判断 None——这是兜底补丁引入的新约定
