# 🧠 Task Planner — 智能任务规划引擎

[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Tests: 274 passed](https://img.shields.io/badge/tests-274%20passed-green.svg)](#-%E5%BC%80%E5%8F%91%E4%B8%8E%E6%B5%8B%E8%AF%95)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-E92063.svg)](https://docs.pydantic.dev/)
[![Async](https://img.shields.io/badge/async-ready-green.svg)](#%E5%85%B3%E4%BA%8E%E5%BC%82%E6%AD%A5%E8%BE%B9%E7%95%8C)

> 基于 **LangGraph** 与 **大语言模型** 的自主任务规划引擎，支持动态 DAG 编排、多模态意图识别与全流程可观测性评估。
> **任务执行链路异步，UI 层保持同步**——边界清晰，可演进。

**工程特性**

- **配置系统** — `cog` 包，scope 分级 + 密钥 `from_env` 强制
- **违禁词库** — AC 自动机 + 白名单子串豁免 + block/review 分级
- **无缝配置抽屉** — 主应用右下角悬浮入口（URL 驱动路由），支持热重载；另保留 `task-planner-config` 独立 CLI 降级路径
- **可选依赖全走优雅降级** — `pyahocorasick` / `ruamel.yaml` / `orjson` / `psutil`
- **后台常驻 event loop** — `async_runtime` 解决 Dash WSGI 一次性 loop 问题
- **Pydantic 值对象** — `IntentModel` / `NodeExecResult`，边界校验 + IDE 补全
- **测试护栏** — 230 单元测试 + 43 MongoDB 集成测试 + redis 集成测试 + 全绿 CI（红黄线分离）

______________________________________________________________________

## ✨ 核心特性

- 🧠 **智能意图识别** — LLM 精准理解需求，自动判断是否需要规划
- 📊 **动态 DAG 生成** — 复杂任务拆解为原子节点，自动构建依赖关系图
- 🔄 **流式执行与实时反馈** — 后台常驻 loop 驱动流式推送，前端轮询更新流程图
- ✅ **节点级人工确认** — 支持标记完成/跳过/失败，精细控制执行流程
- 📜 **完整历史追溯** — 任务持久化，支持断点续传、重新规划
- 🎨 **交互式可视化** — Dash + Cytoscape 流程图交互，节点状态清晰
- 📤 **多格式导出** — SVG / PNG / PDF / DOT / HTML / JSON
- 🔌 **异步边界清晰** — 任务执行走常驻 loop（LLM 异步调用、LangGraph 流式）；UI 层与 DB 客户端同步（避免一次性 loop 陷阱）
- 🧪 **评估套件** — 内置 Crucible 评估引擎，支持 HITL 人工审查
- 🛡 **生产级增强** — 重试、熔断、限流、健康检查、结构化日志
- ⚙️ **配置工具** — 主应用内嵌 URL 驱动，可独立进程运行（端口 8051），自动从 `cog.snapshot()` 生成表单，支持热重载

> 📖 **架构决策详见** [`docs/ARCHITECTURE_FUTURE.md`](docs/ARCHITECTURE_FUTURE.md)（UI/任务层边界）与 [`docs/DESIGN_task_context.md`](docs/DESIGN_task_context.md)（TaskSession / TaskStreamState 双轨）。

______________________________________________________________________

## 🏗️ 架构概览

```text
┌──────────────────────────────────────────────────────────┐
│  Dash UI（新建任务 / 历史记录 / 节点操作）              │
└──────────────────────────┬───────────────────────────────┘
                           │ 轮询 (Interval 1000ms)
┌──────────────────────────▼───────────────────────────────┐
│  Stream Manager                                           │
│  submit() → async_runtime（常驻 loop，独立 daemon 线程）  │
│  TaskStreamState → 前端轮询                               │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  Agent（LangGraph 驱动）                                 │
│  intent → plan → refine → save → render → execute         │
│  （节点 async；执行在常驻 loop 上，支持 cancel_event）   │
└──────┬───────────────────────────────┬───────────────────┘
       │                               │
┌──────▼─────────────┐       ┌─────────▼──────────────────┐
│  LLM Client        │       │  Database                   │
│  AsyncOpenAI+httpx │       │  MongoDB (pymongo 同步)     │
│  （asyncio 持久）  │       │  （同步——见下"关于异步边界"）│
└────────────────────┘       └─────────────────────────────┘

┌───────────────────────────────────────────────┐
│  旁路（可选，独立进程）：                       │
│  ┌────────────────────────┐                    │
│  │  config_ui（端口 8051）│                    │
│  │  只读写 config/*.yaml  │                    │
│  │  和 .env，不连 DB/LLM  │                    │
│  └────────────────────────┘                    │
└───────────────────────────────────────────────┘
```

### 关于异步边界（2026-10-14 重构）

- **Dash WSGI backend 每次回调创建一个一次性 loop**（`asgiref.async_to_sync`），`asyncio.create_task` 的长期任务会在回调返回后立即被取消
- **修复**：引入 `infrastructure/async_runtime.py`——进程级常驻 loop（独立 daemon 线程）
- **DB 客户端改同步 pymongo**：Dash 的 worker 是同步线程，异步 pymongo（Motor）绑定 loop、跨 loop 复用失效；同步版更简单可靠
- **LLM 客户端保持异步**：`AsyncOpenAI` 在常驻 loop 上执行——LLM 调用是真正的长耗时 IO，异步有价值
- **未来切方案 C**（多 worker / 跨进程）时的路径见 `docs/ARCHITECTURE_FUTURE.md`

### 多角色协作的黑板通信模型

```text
TaskState:
  ├── question: str
  ├── tenant_id: str
  ├── plan: Plan                          ← plan 节点写入
  ├── node_results: dict[str, NodeResult] ← execute 节点写入
  ├── intent: IntentModel                 ← intent 节点写入（Pydantic 值对象）
  └── stream_state: TaskStreamState       ← Stream Manager 维护
```

每个节点函数有清晰的输入/输出契约，由 `core/graph/schemas.py` 中的 Pydantic 模型约束。

______________________________________________________________________

## 📁 项目结构

```text
task-planner/
├── CHANGELOG.md
├── .github/workflows/ci.yml
├── config/
│   ├── schema.yaml
│   ├── schema.yaml.example
│   ├── user.yaml
│   └── user.yaml.example
├── data/
│   ├── blocked_words/          # 17 个词库 txt（COVID-19 / GFW / 政治 / 色情 / 暴恐 / 广告 ...）
│   └── whitelist.txt
├── docker-compose.yml
├── docker-compose.prod.yml
├── Dockerfile
├── docs/
│   ├── ARCH_FUTURE.md          # 🔥 改名（原 ARCHITECTURE_FUTURE.md）
│   ├── DESIGN_task_context.md
│   └── adr/
│       ├── 0000-index.md
│       ├── 0001-async-boundary.md
│       ├── 0002-full-chain-async.md
│       ├── 0003-gradio-to-dash.md
│       ├── 0004-langgraph.md
│       ├── 0005-mongodb.md
│       ├── 0006-cog-config.md
│       ├── 0007-python-314.md
│       └── 0008-small-decisions-log.md
├── examples/
│   └── config_hub_usage_example.py
├── LICENSE
├── logs/                        # 🔥 新增：运行时日志
│   ├── task_planner.log
│   └── task_planner.log.{2026-10-25, 10-28, 11-04, 11-09, 11-10}
├── pyproject.toml
├── README.md
├── scripts/
│   ├── audit_private_imports.py
│   ├── bye_bye_cancle_config.sh
│   ├── changelog_migrate.py
│   ├── check_bad_words_len.py
│   ├── check_config_drift.py
│   ├── dev_up.sh
│   ├── find_cyto_configs.py
│   ├── make_future_be_now.py
│   ├── migrate_config_imports.py
│   ├── remove_compat_shells.py
│   └── test_block_words.py
├── src/
│   ├── logs/task_planner.log   # 🔥 新增：src 侧日志
│   ├── task_planner/
│   │   ├── abandon/            # 归档代码（不参与运行）
│   │   │   ├── agent_legacy.py
│   │   │   ├── agent_nah.py
│   │   │   ├── config_old.py
│   │   │   ├── dash_app_legacy.py
│   │   │   ├── database_legacy.py
│   │   │   ├── good_addons_old.py
│   │   │   ├── llm_client_legacy.py
│   │   │   ├── _nodes_legacy.py
│   │   │   └── stream_manager_legacy.py
│   │   ├── config_ui/
│   │   │   ├── app.py
│   │   │   ├── callbacks.py
│   │   │   ├── form_builder.py
│   │   │   ├── layout.py
│   │   │   ├── standalone_app.py    # 🔥 新增
│   │   │   └── writer.py
│   │   ├── core/
│   │   │   ├── db/
│   │   │   │   ├── client.py
│   │   │   │   ├── _helpers.py
│   │   │   │   ├── nodes.py
│   │   │   │   ├── plans.py
│   │   │   │   ├── schema.py
│   │   │   │   └── tasks.py
│   │   │   └── graph/
│   │   │       ├── nodes/
│   │   │       │   ├── cancel.py
│   │   │       │   ├── direct.py
│   │   │       │   ├── execute.py
│   │   │       │   ├── _executor.py
│   │   │       │   ├── _helpers.py
│   │   │       │   ├── intent.py
│   │   │       │   ├── plan.py
│   │   │       │   ├── refine.py
│   │   │       │   ├── render.py
│   │   │       │   ├── retrieve.py     # 🔥 新增（三层降级）
│   │   │       │   ├── routes.py
│   │   │       │   ├── sanitize.py
│   │   │       │   └── save.py
│   │   │       ├── schemas.py
│   │   │       ├── state.py
│   │   │       └── workflow.py
│   │   ├── infrastructure/
│   │   │   ├── assets/
│   │   │   │   ├── cytoscape_js.py
│   │   │   │   └── cytoscape_styles.py
│   │   │   ├── async_runtime.py
│   │   │   ├── blocked_words.py
│   │   │   ├── cog/
│   │   │   │   ├── entry.py
│   │   │   │   ├── guard.py
│   │   │   │   ├── hub.py
│   │   │   │   ├── loader.py
│   │   │   │   ├── registry.py
│   │   │   │   ├── section.py
│   │   │   │   ├── sections/
│   │   │   │   │   ├── base.py
│   │   │   │   │   ├── dashscope.py
│   │   │   │   │   ├── llm.py
│   │   │   │   │   ├── mongo.py
│   │   │   │   │   ├── render.py
│   │   │   │   │   ├── runtime.py
│   │   │   │   │   └── security.py
│   │   │   │   ├── session.py
│   │   │   │   └── store.py
│   │   │   ├── constants.py
│   │   │   ├── llm/
│   │   │   │   ├── api.py
│   │   │   │   ├── client.py
│   │   │   │   ├── core.py
│   │   │   │   ├── errors.py
│   │   │   │   ├── json_utils.py
│   │   │   │   ├── mocks.py
│   │   │   │   └── validator.py
│   │   │   ├── logger_setup.py
│   │   │   ├── prompts/
│   │   │   │   ├── execute_node.txt
│   │   │   │   ├── intent.txt
│   │   │   │   ├── loader.py
│   │   │   │   ├── node_refine.txt
│   │   │   │   └── planner.txt
│   │   │   ├── regexes.py
│   │   │   ├── session_store/
│   │   │   │   ├── in_memory_session_store.py
│   │   │   │   ├── protocol.py
│   │   │   │   └── redis_session_store.py
│   │   │   └── ui_styles.py
│   │   ├── main/
│   │   │   ├── app.py
│   │   │   ├── assets/custom.css
│   │   │   └── ui/
│   │   │       ├── callbacks/
│   │   │       │   ├── export_cb.py
│   │   │       │   ├── graph_js.py
│   │   │       │   ├── history.py
│   │   │       │   └── new_task.py
│   │   │       ├── constants.py
│   │   │       ├── data_ops.py
│   │   │       ├── export.py
│   │   │       └── layout.py
│   │   ├── py.typed
│   │   ├── services/
│   │   │   ├── agent/
│   │   │   │   ├── commands.py
│   │   │   │   ├── session.py
│   │   │   │   ├── state.py
│   │   │   │   ├── stream.py
│   │   │   │   └── view.py
│   │   │   ├── stream/
│   │   │   │   ├── cleaner.py
│   │   │   │   ├── control.py
│   │   │   │   ├── launcher.py
│   │   │   │   ├── state.py
│   │   │   │   └── view.py
│   │   │   └── view_model.py
│   │   └── utils/
│   │       ├── context.py
│   │       ├── cytoscape_adapter.py
│   │       ├── flowchart_pro.py
│   │       ├── good_addons.py
│   │       ├── presentation_utils.py
│   │       └── pyvis_export.py
│   └── task_planner.egg-info/
├── tests/
│   ├── conftest.py
│   ├── eval_suite/
│   │   ├── conftest.py
│   │   ├── test_deterministic.py
│   │   ├── test_deterministic_scoring.py
│   │   ├── test_golden_generator_sanity.py
│   │   ├── test_jury.py
│   │   ├── test_runner_resilience.py
│   │   └── test_validator_edge_cases.py
│   ├── evaluation/evaluators.py
│   └── test_package/
│       ├── test_commands_contract.py
│       ├── test_config_contract.py
│       ├── test_evaluators.py
│       ├── test_execute.py
│       ├── test_executor_coerce.py
│       ├── test_generate_plan.py       # 🔥 新增
│       ├── test_graph_finished.py
│       ├── test_initial_state.py
│       ├── test_json_extraction.py
│       ├── test_mongodb.py
│       ├── test_normalize_output.py
│       ├── test_plan.py                # 🔥 新增
│       ├── test_redis_session_store.py
│       ├── test_refine.py
│       ├── test_resolve_target_index.py # 🔥 新增
│       ├── test_routes.py
│       ├── test_save.py
│       ├── test_session_store.py
│       └── test_state_defaults.py
└── tools/                              # 🔥 新增顶层目录
    ├── __init__.py
    └── eval_suite/
        ├── cli.py
        ├── errors.py
        ├── __main__.py                 # 🔥 支持 `python -m tools.eval_suite`
        ├── types.py
        ├── golden/
        │   ├── generator.py
        │   └── validator.py
        ├── hil/
        │   ├── dash_app.py
        │   └── sheet.py
        ├── jury/
        │   └── calibrated_judge.py
        ├── report/
        │   ├── html_report.py
        │   └── json_report.py
        ├── runners/
        │   └── agent_runner.py
        └── scorers/
            ├── deterministic.py
            └── llm_judge.py```

______________________________________________________________________

## 🚀 快速开始

### 环境要求

- **Python 3.14+**（`StrEnum` / `NotRequired` / PEP 649 / PEP 695 原生）
- **MongoDB**（本地或远程）
- **LLM API Key**（OpenAI 兼容接口，如 DashScope）

### 依赖项

- 使用 `pymongo>=4.5.0`（同步——原因见"关于异步边界"）；**已移除 `motor`**
- 可选加速：`orjson`、`pyahocorasick`、`ruamel.yaml`、`psutil`

### 1. 安装

```bash
git clone https://github.com/Duckherewegoo/task-planner.git task-planner
cd ./task-planner

# 核心依赖
pip install -e .

# 可选加速 + 开发依赖
pip install -e ".[orjson,dev]"
```

### 2. 起 MongoDB

**方式 A：Docker（推荐）**

```bash
docker run -d --name task_mongodb -p 27017:27017 mongo:7.0
```

**方式 B：本地 mongod**

```bash
sudo systemctl start mongod
```

**完整使用流程**

```bash
# ── 开发模式（只 mongodb）──
docker compose up -d
task-planner                        # 主应用在宿主机跑

# ── 生产模式（全容器）──
export DASHSCOPE_API_KEY=sk-xxx
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# ── 生产 + 配置工具 ──
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    --profile config up -d

# ── 看日志 ──
docker compose logs -f app

# ── 停 ──
docker compose down                 # 停服务，保留数据卷
docker compose down -v              # 停服务 + 删数据卷（危险）
```

**关于 .env 的说明**

- **生产**：`.env` 放宿主机（不进镜像），compose 用它注入环境变量。关键：`.dockerignore` 里已排除 `.env`，不会误打进镜像。
- **配置工具**：config-ui 服务挂载 `.env` 到容器里可写（因为工具要能改密钥）。生产环境慎用——可以只在内网开。

### 3. 配置 `.env`

在项目根目录创建 `.env`（参考 `.env.example`）：

```bash
# ── LLM（密钥类字段：必须从环境变量读，禁止落盘到 YAML）──
DASHSCOPE_API_KEY=your_api_key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
USE_MOCK_LLM=false              # 开发调试可设 true，跳过真实调用

# ── MongoDB ──
MONGO_HOST=localhost            # ⚠️ Docker Compose 网络里可用 "task_mongodb"
MONGO_PORT=27017
MONGO_DB=task_planner_db       # ⚠️ 集成测试库名须以 _test 结尾，否则跳过

# ── 日志 ──
DEBUG=false                     # 默认 false（生产安全）；调试时设 true
LOG_CONSOLE_LEVEL=INFO

# ── Checkpointer ──
CHECKPOINTER_TYPE=memory        # memory / sqlite / postgres

# ── 说明 ──
# 其他普通配置（超时、并发、端口等）请改 config/schema.yaml。
# 用户级偏好（主题、UI 端口）请改 config/user.yaml，见该文件内注释。
```

### 4. 启动

```bash
# 方式 A：pip 安装的入口
task-planner

# 方式 B：模块运行
python -m task_planner.main.dash_app
```

浏览器访问 <http://localhost:8050>。

> ⚠️ **端口说明**：Dash 默认端口是 **8050**。7860 是 Gradio 的默认端口，**本项目已弃用 Gradio**，不要混用。

### 5. 修改配置（两种方式）

**日常使用（推荐）：**
直接在主应用（端口 8050）点击右下角的 **⚙️ 悬浮按钮**，即可无缝滑出配置抽屉，修改后点击保存即可（支持 `user.yaml` 和 `.env` 热重载）。

**独立工具（降级/运维）：**
如果主应用挂了，或者你想单独打开一个干净的配置面板：

```bash
task-planner-config
```

浏览器访问 <http://127.0.0.1:8051>。

**功能：**

- 自动从 `cog.hub.snapshot()` 生成表单（新增字段无需改工具代码）
- 普通配置写 `user.yaml`，密钥写 `.env`
- 可点「重新加载」从磁盘重读（内部调用 `hub.reload()`）
- 保存后主应用需重启生效
- 与主应用完全隔离：不 import 主应用、不连 DB、不连 LLM

**端口覆盖：**

```bash
CONFIG_UI_HOST=127.0.0.1
CONFIG_UI_PORT=8051
```

______________________________________________________________________

## 🎮 使用示例

### 1. 新建任务

输入示例：

> "教我做甜口西红柿炒鸡蛋，需要详细步骤"

点击 **🚀 开始规划**，系统会：

1. 识别意图（是否需要规划）
1. 生成 DAG（节点 + 依赖边）
1. 细化每个节点的操作细节（如启用）
1. 渲染交互式流程图

### 2. 节点操作

- 点击流程图节点 → 右侧显示前置条件、操作细节、后置条件
- **▶️ 开始执行** 运行根节点，执行完成后进入 interrupt 等待用户确认
- **✅ 完成 / ⏭️ 跳过 / ❌ 失败** 单节点操作
- 系统自动解锁下游（`done` 解锁 hard 依赖，`failed` 仅解锁 soft 边）

### 3. 历史记录

- **继续执行**：从中断点恢复
- **重新规划**：基于原始需求重新生成
- **修改规划**：微调后重新规划
- **重试失败节点**：自动重试失败节点
- **批量删除**：清除历史数据

### 4. 数据导出

- 图像：PNG / SVG
- 数据：JSON / DOT / HTML（自包含交互式流程图）

### 5. 配置修改

三种方式，按用户身份选择：

| 身份 | 方式 | 影响范围 |
|---|---|---|
| 用户 | 启动 `task-planner-config` → 改字段 → 点保存 | 只影响 `config/user.yaml` 和 `.env` |
| 用户 | 编辑 `config/user.yaml` | 仅 `scope=user` 的字段生效（防越权） |
| 开发者 | 编辑 `config/schema.yaml`（值 + purpose + scope + type） | 新增字段自动出现在配置工具里 |
| 运维 | 环境变量 | 密钥类字段（`from_env=true`）只能从环境变量读；`.env` / Docker env / K8s Secret 都行 |

______________________________________________________________________

## ⚙️ 配置说明

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DASHSCOPE_API_KEY` | LLM API Key | 必填 |
| `DASHSCOPE_BASE_URL` | API Base URL | DashScope 兼容模式 |
| `USE_MOCK_LLM` | 跳过真实 LLM 调用 | `false` |
| `MONGO_HOST` | MongoDB 主机 | `localhost`（宿主机跑应用时）；Docker 网络内用 `task_mongodb` |
| `MONGO_PORT` | MongoDB 端口 | `27017` |
| `MONGO_DB` | 数据库名 | `task_planner_db`（`*_test` 结尾才跑集成测试） |
| `DEBUG` | 调试模式 | `false` |
| `LOG_CONSOLE_LEVEL` | 控制台日志级别 | `INFO`（`DEBUG=true` 时为 `DEBUG`） |
| `CHECKPOINTER_TYPE` | LangGraph checkpoint 后端 | `memory` |
| `CHECKPOINT_DB_PATH` | SQLite 路径（type=sqlite 时） | `checkpoints.db` |
| `LLM_MAX_CONCURRENT` | 最大并发 LLM 请求 | `10` |
| `LLM_TIMEOUT` | LLM 请求超时（秒） | `120` |
| `LLM_MAX_RETRIES` | 重试次数 | `3` |
| `LLM_NODE_TIMEOUT` | 单节点执行超时（秒） | `60` |
| `DASH_HOST` | Dash 监听地址 | `0.0.0.0` |
| `DASH_PORT` | Dash 监听端口 | `8050` |

### 配置读取顺序

```text
环境变量（仅 from_env=true 的字段，如密钥）
        ↓ 覆盖
config/user.yaml（仅 scope=user 的字段）
        ↓ 覆盖
config/schema.yaml（默认值）
```

**密钥类字段（如 `DASHSCOPE_API_KEY`）永远不落盘**，只从环境变量读——
`schema.yaml` 里以 `from_env: true` 声明，运行时经 `ConfigEntry` 护栏校验。

完整配置声明见 `config/schema.yaml`；
配置中枢代码见 `src/task_planner/infrastructure/cog/`。

**推荐操作方式：**

- **[图形化]** `task-planner-config`（自动生成表单）
- **[手改]** `config/user.yaml`（普通）+ `.env`（密钥）
- **[代码引用]** `from task_planner.infrastructure.cog import hub`
  - `hub.user.THEME` / `hub.dev.LLM_TIMEOUT`

______________________________________________________________________

## 🧪 开发与测试

### 测试

```bash
# 默认：只跑单元 + 非 eval + 非 integration（< 6 秒）
pytest

# 跑 MongoDB 集成测试（需要 docker compose up -d mongodb）
MONGO_DB=task_planner_test pytest tests/test_package/test_mongodb.py -m integration -v

# 跑 eval 评估套件（走真实 LLM，慢、耗 token）
pytest -m eval

# eval 走 Mock 模式（免费验证流程）
USE_MOCK_LLM=true pytest -m eval

# 只跑单元测试
pytest -m unit
```

**测试分层说明：**

| Marker | 说明 |
|---|---|
| `unit` | 纯逻辑测试，无 I/O，无 LLM，无 DB |
| `integration` | 集成测试，需外部服务（MongoDB 等）——共 48 个（43 MongoDB + 5 其他），日常 `pytest` 默认排除 |
| `eval-live` | 评估套件，默认走真实 LLM |
| `slow` | 慢速测试 |

**当前测试规模**：183 passed（单元）+ 48 deselected（integration / eval_live）

### 代码质量

```bash
# 格式化
black src/ tests/
ruff check --fix src/ tests/

# 类型检查
pyright src/

# 一键全绿
black src/ tests/ && ruff check --fix src/ tests/ && pytest
```

### 端到端评估

```bash
pytest                              # 不跑 eval_live
pytest -m eval_live --eval-live    # 只跑真实 LLM 评估
python -m task_planner.eval_suite.cli run --mock     # CLI 方式
python -m task_planner.eval_suite.hil.dash_app       # HIL 前端
```

______________________________________________________________________

## 🔧 故障排查

### ❌ `NameError: name 'task' is not defined`（已修复）

**原因**：`services/agent/stream.py` 曾引用 `session.attach_runner(task)`，但 `task` 变量在该作用域不存在。

**修复**（2026-10-14）：删除该行——runner task 的生命周期由 `TaskStreamState` 管理。

### ❌ `Event loop is closed` 或后台任务被静默取消

**原因**：Dash WSGI backend 每次 async 回调创建一个一次性 loop。`asyncio.create_task` 的长期任务会在回调返回后立即被取消。

**修复**（2026-10-14）：所有需要跨回调存活的任务必须通过 `infrastructure/async_runtime.submit()` 提交到常驻 loop。

**验证是否生效**：

```bash
grep -rn "asyncio.create_task" src/task_planner/services/ --include="*.py" | grep -v test
# 期望：无输出（全部改为 async_runtime.submit）
```

### ❌ `[DB] ✅ MongoDB 连接成功` 日志看不到

**原因**：`logger_setup.py` 曾忽略 `LOG_CONSOLE_LEVEL` 配置（用 `DEBUG ? DEBUG : WARNING` 硬编码）。

**修复**（2026-10-14）：新增 `_resolve_console_level()`——优先级 `LOG_CONSOLE_LEVEL > (DEBUG ? DEBUG : WARNING)`。

**确认配置**：`.env` 里 `LOG_CONSOLE_LEVEL=INFO`。

### ❌ `ModuleNotFoundError: No module named 'task_planner'`

**原因**：未安装包或用了错误的 Python 解释器。

```bash
# 确认安装
pip show task-planner

# 或用模块方式运行
python -m task_planner.main.dash_app
```

### 端口占用 / 启动失败

```bash
# 查看谁占用了 8050
lsof -ti:8050

# 杀掉占用进程
kill $(lsof -ti:8050)

# 或改端口
echo "DASH_PORT=8051" >> .env
```

### ❌ 浏览器打开但点击无反应

**排查**：F12 → Console 看 JS 报错；Network 看 `_dash-update-component` 请求。

**常见**：`assets/custom.css` 404（检查 `assets_folder` 路径）。

### ⚠️ 日志目录出现在 3 个地方

现象：

```text
./logs/task_planner.log
./src/logs/task_planner.log
./src/task_planner/logs/task_planner.log
```

**原因**：`LOG_DIR` 是相对路径，从不同 CWD 启动会写到不同位置。

**修复**（推荐）：`.env` 里显式指定绝对路径：

```bash
LOG_DIR=/abs/path/to/project/logs
```

清掉历史残留：

```bash
rm -rf src/logs/ src/task_planner/logs/
```

### ⚠️ 日志报 `违禁词库目录不存在`

**原因**：`data/blocked_words/` 目录为空或路径不对。

**修复**：

```bash
# 确认词库就位
ls data/blocked_words/ | head

# 确认加载器生效
python -c "
from task_planner.infrastructure.blocked_words import BLOCKED_WORDS
print(f'词条数: {len(BLOCKED_WORDS)}')
"

# 如需自定义路径
export BLOCKED_WORDS_DIR=/mnt/nfs/security/words
```

### ⚠️ `task-planner-config` 命令找不到

**原因**：`pyproject.toml` 的 `[project.scripts]` 未生效。

**修复**：

```bash
# 重新安装注册入口点
pip install -e .

# 确认入口点已注册
which task-planner-config
```

或直接用模块运行：

```bash
python -m task_planner.config_ui.app
```

### ⚠️ 配置工具改了值，主应用没反应

**原因**：两个进程持有各自的 hub 实例，通过文件系统间接通信。

**修复**：

- 主应用需重启才读新值（当前设计如此）
- 确认配置文件写对：

```bash
cat config/user.yaml
cat .env
```

### ⚠️ 配置工具显示的还是旧值

**原因**：hub 是「启动快照」，不实时刷盘。

**修复**：点配置工具的「🔄 重新加载」按钮，它会调 `hub.reload()`，从磁盘重读。

______________________________________________________________________

## 🤝 贡献

欢迎 Issue 和 PR。提交前请确保：

- `pytest` 全绿
- `ruff check` 无警告
- `pyright` 无实质影响 error （因为缺少stub或第三方库本身引入的any报错可以忽略）
- 新增功能附带测试，保证功能可用，不影响主体运行

______________________________________________________________________

## 🙏 致谢

本项目基于以下优秀技术构建：

- [LangGraph](https://langchain-ai.github.io/langgraph/) — 有向图状态机编排
- [Dash](https://dash.plotly.com/) / [Cytoscape](https://js.cytoscape.org/) — 交互式可视化
- [pymongo](https://pymongo.readthedocs.io/) — 同步 MongoDB 驱动
- [AsyncOpenAI](https://github.com/openai/openai-python) / [httpx](https://www.python-httpx.org/) — 高性能异步 LLM 调用
- [pyvis](https://pyvis.readthedocs.io/) / [pydot](https://pypi.org/project/pydot/) / [CairoSVG](https://cairosvg.org/) — 图渲染与导出
- [ruamel.yaml](https://pypi.org/project/ruamel.yaml/)（可选）— 配置工具保留 YAML 注释
- [Pydantic](https://docs.pydantic.dev/) — 值对象与配置校验

核心贡献者：**DakeGinger**

______________________________________________________________________

## 📄 许可证

本项目以 **GNU General Public License v3.0** 发布，详见 [LICENSE](LICENSE) 与 [官方文本](https://www.gnu.org/licenses/gpl-3.0)。

- 使用、复制、修改、分发本软件均须遵守 GPL v3 条款
- 修改后的源码在分发时**必须**以相同许可证开源
- 本软件按"原样"提供，**不附带任何担保**，作者不对使用后果承担责任
- 完整的权限、条件与免责声明以 [LICENSE](LICENSE) 文件为准

______________________________________________________________________

## ℹ️ 已知问题

### 关于"全链路异步"

项目最初宣称"全链路异步化"。2026-10-14 实测发现：**Dash 4.4.1 + `dash[async]` 的 Flask backend 用 `asgiref.async_to_sync` 驱动 async 回调，同 thread 内 event loop 会变化**（实测 loop id 序列 A → B → C）。因此：

- **异步 client（Motor）无法跨回调复用**——每回调重建 → 连接池爆炸
- **同步 client（pymongo）不绑 loop**——一次创建永久复用

**当前策略**（见 `docs/ARCHITECTURE_FUTURE.md`）：

| 层 | 选型 | 理由 |
|---|---|---|
| UI 回调 | 声明 async，内部同步 | 兼容 Dash WSGI |
| 后台任务 | `async_runtime` 常驻 loop | 需要跨回调存活 |
| DB 客户端 | 同步 pymongo | 不绑 loop；worker 是同步线程 |
| LLM 客户端 | AsyncOpenAI | 真异步调用，跑在常驻 loop 上 |

**未来切 ASGI 后端**（`dash[async]` 或直接 FastAPI）后，可将 DB 客户端切回异步——切换路径见 `docs/ARCHITECTURE_FUTURE.md`。

### 关于 `CHANGELOG.md`

项目采用 **per-module changelog**——每个模块的历史变更集中在 `CHANGELOG.md`。源文件顶部只保留：

- 模块职责说明
- 最新一条变更
- 指针：`⚠️ 完整变更历史见 CHANGELOG.md。`

**目的**：源文件可读性提升；历史追溯仍可查（`CHANGELOG.md` + `git log`）。

### 关于 `good_addons.py` 的 Changelog

它是"独立 drop-in 库"——不参与 Changelog 迁移，Changelog 保留在文件内。**理由**：它设计为可复制到任何项目，文件内保留完整历史是合理的。

### 关于 `abandon/` 目录

保留归档，不参与运行。理由：

- 出奇怪 bug 时可 diff 出「是否重构引入」
- `scripts/check_config_drift.py --old` 仍用它做基线
- `pyproject.toml` 里 `[tool.setuptools.packages.find]` 设了
  `exclude = ["task_planner.abandon*"]`，不会打包进 wheel

**不要 ignore**：`.gitignore` 里不要加 `abandon/`。

**不要 import**：`src/` 下所有代码不引用 `abandon.*`。

**不要迁移 Changelog**：`scripts/changelog_migrate.py` 已排除 `abandon/*`。

______________________________________________________________________

## ℹ️ 更新日志（项目级）

| 日期 | 主题 | 影响 |
|---|---|---|
| 2026-10-14 | `async_runtime` 常驻 loop 修复 Dash 一次性 loop | `stream/launcher` / `stream/cleaner` / `agent/*` |
| 2026-10-14 | DB 客户端从 Motor 迁移到同步 pymongo | `core/db/*` |
| 2026-10-14 | Changelog 集中至 `CHANGELOG.md` | 11 个源文件 |
| 2026-10-14 | `set_req_id` 有值复用——修复同任务多 req_id | `logger_setup.py` |
| 2026-10-14 | Pydantic 值对象引入（`IntentModel` / `NodeExecResult`） | `schemas.py` / `_executor.py` |
| 2026-10-14 | MongoDB 集成测试（43 个） | `tests/test_package/test_mongodb.py` |
| 2026-10-14 | `TaskSession` 死字段清理 + 双轨边界文档化 | `agent/session.py` / `docs/DESIGN_task_context.md` |

完整历史：`CHANGELOG.md` + `git log`
决策历史: `docs/adr/0000-index.md`索引

______________________________________________________________________

Made with ❤️ by JiangDake
