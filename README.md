# 🧠 Task Planner — 智能任务规划引擎

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Async](https://img.shields.io/badge/async-ready-green.svg)](#)

> 基于 **LangGraph** 与 **大语言模型** 的自主任务规划引擎，支持动态 DAG 编排、多模态意图识别与全流程可观测性评估。
> **全链路异步化，高性能、可扩展。**

**工程特性：**

- **配置系统** — `cog` 包，scope 分级 + 密钥 `from_env` 强制
- **违禁词库** — AC 自动机 + 白名单子串豁免 + block/review 分级
- **独立配置工具** — `task-planner-config`（可选套件，端口 8051）
- **可选依赖全走优雅降级** — `pyahocorasick` / `ruamel.yaml` / `orjson` / `psutil`

______________________________________________________________________

## ✨ 核心特性

- 🧠 **智能意图识别** — LLM 精准理解需求，自动判断是否需要规划
- 📊 **动态 DAG 生成** — 复杂任务拆解为原子节点，自动构建依赖关系图
- 🔄 **流式执行与实时反馈** — 异步流式推送进度，前端实时更新流程图
- ✅ **节点级人工确认** — 支持标记完成/跳过/失败，精细控制执行流程
- 📜 **完整历史追溯** — 任务持久化，支持断点续传、重新规划
- 🎨 **交互式可视化** — Dash + Cytoscape 流程图交互，节点状态清晰
- 📤 **多格式导出** — SVG / PNG / PDF / DOT / HTML / JSON
- 🔌 **异步优先** — 全链路 async I/O（LLM、DB、流式输出）
- 🧪 **评估套件** — 内置 Crucible 评估引擎，支持 HITL 人工审查
- 🛡 **生产级增强** — 重试、熔断、限流、健康检查、结构化日志
- ⚙️ **独立配置工具** — 可选套件，独立进程运行（端口 8051），自动从 `cog.snapshot()` 生成表单，支持热重载

> 注：Gradio 已完全弃用，保留相关说明以便排查旧配置。

______________________________________________________________________

## 🏗️ 架构概览

```
┌──────────────────────────────────────────────────────────┐
│  Dash UI（新建任务 / 历史记录 / 节点操作）              │
└──────────────────────────┬───────────────────────────────┘
                           │ 轮询 (Interval 500ms)
┌──────────────────────────▼───────────────────────────────┐
│  Stream Manager                                           │
│  后台 asyncio.Task → TaskStreamState → 前端轮询           │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│  Agent（LangGraph 驱动）                                 │
│  intent → plan → refine → save → render → execute         │
│  （全部异步，支持 asyncio.Event 取消信号）               │
└──────┬───────────────────────────────┬───────────────────┘
       │                               │
┌──────▼─────────────┐       ┌─────────▼──────────────────┐
│  LLM Client        │       │  Database                   │
│  AsyncOpenAI+httpx │       │  MongoDB (Motor 异步)       │
└────────────────────┘       └─────────────────────────────┘

┌───────────────────────────────────────────────┐
│  旁路（可选，独立进程）：                       │
│  ┌────────────────────────┐                    │
│  │  config_ui（端口 8051）│                    │
│  │  task-planner-config   │                    │
│  │  只读写 config/*.yaml  │                    │
│  │  和 .env，不连 DB/LLM  │                    │
│  └────────────────────────┘                    │
└───────────────────────────────────────────────┘
```

______________________________________________________________________

## 📁 项目结构

```
new_task_planner/
├── .github/
│   └── workflows/
│       └── ci.yml                    ← ci action
├── Dockerfile                        ← mongodb mode
├── .dockerignore                     ← dockerignore
├── docker-compose.yml                ← 只留 mongodb
├── docker-compose.prod.yml           ← docker mode 
├── config/                        # 🔴 配置数据（外部，可挂载）
│   ├── schema.yaml                # 开发者声明（value / purpose / scope / secret / from_env）
│   ├── schema.yaml.example        # 模板示例
│   ├── user.yaml                  # 用户覆盖（仅 scope=user 生效）
│   └── user.yaml.example          # 模板示例
│
├── data/                          # 🔴 数据（外部）
│   ├── blocked_words/             # 违禁词库（17 个 txt，扁平）
│   │   ├── 反动词库.txt           # → tier=block
│   │   ├── 暴恐词库.txt           # → tier=block
│   │   ├── 色情词库.txt           # → tier=block
│   │   ├── 涉枪涉爆.txt           # → tier=block
│   │   ├── COVID-19词库.txt       # → tier=review
│   │   ├── GFW补充词库.txt        # → tier=review
│   │   ├── 其他词库.txt / 广告类型.txt / 政治类型.txt
│   │   ├── 新思想启蒙.txt / 民生词库.txt
│   │   ├── 网易前端过滤敏感词库.txt
│   │   ├── 色情类型.txt / 补充词库.txt / 贪腐词库.txt
│   │   ├── 零时-Tencent.txt / 非法网址.txt
│   └── whitelist.txt              # 白名单（子串豁免）
│
├── scripts/                       # 🟡 运维与检查脚本
│   ├── check_config_drift.py      # 配置漂移检测
│   ├── migrate_config_imports.py  # 旧 config 引用迁移
│   ├── check_bad_words_len.py     # 违禁词长度分布诊断
│   ├── test_block_words.py        # 词库加载 + 匹配测试
│   └── find_cyto_configs.py       # Cytoscape 配置搜索
│
├── examples/
│   └── config_hub_usage_example.py  # cog 用法示例
│
├── src/task_planner/
│   ├── abandon/                   # 🗄️ 归档（不参与运行，9 文件）
│   │   ├── config_old.py          # 旧大宗 config.py
│   │   ├── agent_legacy.py / agent_nah.py
│   │   ├── dash_app_legacy.py
│   │   ├── llm_client_legacy.py
│   │   ├── stream_manager_legacy.py
│   │   ├── datebase_legacy.py     # ⚠️ 原拼写保留
│   │   ├── good_addons_old.py
│   │   └── _nodes_legacy.py
│   │
│   ├── config_ui/                 # ✅ 独立配置工具
│   │   ├── __init__.py            # 导出 app / main
│   │   ├── app.py                 # Dash 实例 + main()，默认端口 8051
│   │   ├── layout.py              # 页面布局
│   │   ├── form_builder.py        # 从 cog.hub 自动生成表单
│   │   ├── writer.py              # 原子写 user.yaml / .env
│   │   └── callbacks.py           # 保存 / 重置 / 重载
│   │
│   ├── core/
│   │   ├── database.py            # 【兼容壳】→ core/db/
│   │   ├── db/                    # MongoDB 数据层（6 文件）
│   │   │   ├── client.py          # 连接生命周期 + 索引
│   │   │   ├── schema.py          # 数据清洗 / status 辅助 / validate_plan
│   │   │   ├── plans.py           # Plan CRUD
│   │   │   ├── tasks.py           # Task CRUD + 4 状态更新
│   │   │   ├── nodes.py           # nodes 数组操作
│   │   │   └── manager.py         # DBManager 兼容类
│   │   └── graph/
│   │       ├── state.py           # TaskState
│   │       ├── workflow.py         # 图拓扑 + Checkpointer + 单例
│   │       └── nodes/             # 图节点（13 文件）
│   │           ├── intent.py / plan.py / refine.py
│   │           ├── save.py / render.py / execute.py
│   │           ├── direct.py / cancel.py
│   │           ├── routes.py      # 路由函数（纯计算）
│   │           ├── sanitize.py    # 横切：脱敏 / 视图过滤
│   │           └── _executor.py   # 单节点执行器
│   │
│   ├── infrastructure/
│   │   ├── assets/                # Cytoscape 资源
│   │   │   ├── cytoscape_styles.py   # 30+ 规则样式表
│   │   │   └── cytoscape_js.py       # 3 个 JS 模板 + GRAPH_CONFIGS
│   │   ├── cog/                   # 配置中枢（拆分包）
│   │   │   ├── entry.py           # ConfigEntry + Scope + Role
│   │   │   ├── store.py           # CRUD（增删改查）+ clear
│   │   │   ├── guard.py           # 权限规则
│   │   │   ├── loader.py          # YAML → ConfigEntry（支持 from_env）
│   │   │   ├── session.py         # 按角色代理 CRUD
│   │   │   ├── section.py         # ConfigSection 抽象基类
│   │   │   ├── registry.py        # @register_section 装饰器
│   │   │   ├── hub.py             # 门面 + reload()
│   │   │   ├── __init__.py        # 组装 + reload_hub()
│   │   │   └── sections/          # 8 个内置 section
│   │   │       ├── base.py / dashscope.py / llm.py / mongo.py
│   │   │       └── render.py / runtime.py / security.py / web.py
│   │   ├── llm/                   # LLM 调用（7 文件）
│   │   │   ├── client.py          # AsyncOpenAI 单例
│   │   │   ├── core.py            # 单次调用（重试 / 超时 / 流式）
│   │   │   ├── api.py             # 5 个业务接口
│   │   │   ├── json_utils.py / validator.py
│   │   │   ├── mocks.py / errors.py
│   │   ├── prompts/               # Prompt 资源
│   │   │   ├── loader.py          # 加载 .txt → Template + render_template
│   │   │   ├── intent.txt / planner.txt
│   │   │   └── node_refine.txt / execute_node.txt
│   │   ├── constants.py           # 枚举 / 状态码 / 颜色
│   │   ├── regexes.py             # 预编译正则
│   │   ├── ui_styles.py           # Dash 内联样式
│   │   ├── blocked_words.py       # 违禁词库（AC自动机 + 白名单）
│   │   ├── logger_setup.py        # 异步日志（队列 + 线程）
│   │   ├── session_store.py       # 会话存储接口（Protocol）
│   │   └── llm_client.py          # 【兼容壳】→ infrastructure/llm/
│   │
│   ├── services/
│   │   ├── agent/                 # Agent 主入口（5 文件）
│   │   │   ├── session.py         # TaskSession + 会话池
│   │   │   ├── state.py           # make_initial_state
│   │   │   ├── stream.py          # run_task_stream
│   │   │   ├── commands.py        # 5 个命令
│   │   │   └── view.py            # 快照提取（is_graph_finished）
│   │   ├── stream/                # 流式任务管理（5 文件）
│   │   │   ├── state.py           # TaskStreamState
│   │   │   ├── cleaner.py         # StreamStateCleaner + state_cleaner
│   │   │   ├── launcher.py / control.py
│   │   │   └── view.py
│   │   ├── agent.py               # 【兼容壳】→ services/agent/
│   │   ├── stream_manager.py      # 【兼容壳】→ services/stream/
│   │   └── view_model.py          # 前端字段投影
│   │
│   ├── utils/                     # 独立工具（不拆，KISS 加固）
│   │   ├── context.py             # OperationCancelled + cancel_event
│   │   ├── cytoscape_adapter.py   # DAG → Cytoscape 元素
│   │   ├── flowchart_pro.py       # 交互式 HTML 渲染
│   │   ├── pyvis_export.py        # 静态导出（SVG/PNG/PDF/DOT）
│   │   ├── presentation_utils.py  # 展示辅助
│   │   └── good_addons.py         # 运行时增强层（drop-in）
│   │
│   ├── main/
│   │   ├── ui/                    # Dash UI（拆分包）
│   │   │   ├── app.py             # Dash 实例 + main()
│   │   │   ├── layout.py          # 页面布局
│   │   │   ├── constants.py       # HistorySelectResult / 缓存
│   │   │   ├── data_ops.py        # 数据解析 / 节点操作
│   │   │   ├── export.py          # JSON / DOT / HTML / SVG / PNG
│   │   │   └── callbacks/         # 4 个分组回调
│   │   │       ├── new_task.py / history.py
│   │   │       ├── export_cb.py / graph_js.py
│   │   ├── dash_app.py            # 【兼容壳】→ main/ui/
│   │   ├── assets/custom.css
│   │   └── py.typed
│   │
│   └── logs/                      # ⚠️ 见下方"已知问题"
│
├── tests/
│   ├── conftest.py
│   ├── test_package/              # 单元 + 集成（7 文件，77 测试）
│   │   ├── test_config_contract.py
│   │   ├── test_evaluators.py
│   │   ├── test_graph_finished.py
│   │   ├── test_initial_state.py
│   │   ├── test_json_extraction.py
│   │   ├── test_normalize_output.py
│   │   └── test_session_store.py
│   └── eval_suite/                # Crucible 评估套件
│       ├── crucible_eval.py / dataset_manager.py
│       ├── hitl_reviewer.py / run_evaluation.py
│       └── harness/
│           ├── agent_harness.py / evaluators.py / reporters.py
│
├── logs/                          # 运行日志（项目根）
├── eval_reports/                  # 评估报告输出
├── pyproject.toml
├── README.md
├── LICENSE
└── .gitignore
```

______________________________________________________________________

## 🚀 快速开始

### 环境要求

- **Python 3.10+**
- **MongoDB**（本地或远程）
- **LLM API Key**（OpenAI 兼容接口，如 DashScope）

### 1. 安装

```bash
git clone https://github.com/Duckherewegoo/My-AI-Journey.git
cd ....../new_task-planner

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
task-planner                    # 主应用在宿主机跑

# ── 生产模式（全容器）──
export DASHSCOPE_API_KEY=sk-xxx
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# ── 生产 + 配置工具 ──
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    --profile config up -d

# ── 看日志 ──
docker compose logs -f app

# ── 停 ──
docker compose down                # 停服务，保留数据卷
docker compose down -v             # 停服务 + 删数据卷（危险）

```

**关于 .env 的说明**

- 生产：.env 放宿主机（不进镜像），compose 用它注入环境变量。关键：.dockerignore 里已排除 .env，不会误打进镜像。

- 配置工具：config-ui 服务挂载 .env 到容器里可写（因为工具要能改密钥）。生产环境慎用——可以只在内网开。

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
MONGO_DB=task_planner_db

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

浏览器访问 http://localhost:8050 。

> ⚠️ **端口说明**：Dash 默认端口是 **8050**（不是 7860）。7860 是 Gradio 的默认端口，本项目已弃用 Gradio。

### 5. 启动配置工具（可选）

```bash
# 方式 A：pip 安装的入口（推荐）
task-planner-config

# 方式 B：模块运行
python -m task_planner.config_ui.app
```

浏览器访问 http://127.0.0.1:8051 。

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

> “教我做甜口西红柿炒鸡蛋，需要详细步骤”

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

**【用户】走图形界面** — 启动 `task-planner-config` → 改字段 → 点保存；只影响 `config/user.yaml` 和 `.env`。

**【用户】手改 YAML** - 编辑 `config/user.yaml`；仅 `scope=user` 的字段生效（防越权）。

**【开发者】改 schema** - 编辑 `config/schema.yaml`（值 + purpose + scope + type）；新增字段自动出现在配置工具里。

**【运维】环境变量** - 密钥类字段（`from_env=true`）只能从环境变量读；`.env` / Docker env / K8s Secret 都行。

______________________________________________________________________

## ⚙️ 配置说明

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | LLM API Key | 必填 |
| `DASHSCOPE_BASE_URL` | API Base URL | DashScope 兼容模式 |
| `USE_MOCK_LLM` | 跳过真实 LLM 调用 | `false` |
| `MONGO_HOST` | MongoDB 主机 | `task_mongodb` |
| `MONGO_PORT` | MongoDB 端口 | `27017` |
| `MONGO_DB` | 数据库名 | `task_planner_db` |
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

```
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
# 默认：只跑单元 + 非 eval 测试（< 20 秒）
pytest

# 跑 eval 评估套件（走真实 LLM，慢、耗 token）
pytest -m eval

# eval 走 Mock 模式（免费验证流程）
USE_MOCK_LLM=true pytest -m eval

# 只跑单元测试
pytest -m unit
```

**测试分层说明：**

| Marker | 说明 |
| --- | --- |
| `unit` | 纯逻辑测试，无 I/O，无 LLM，无 DB |
| `integration` | 集成测试，需外部服务（MongoDB 等） |
| `eval` | 评估套件，默认走真实 LLM |
| `slow` | 慢速测试 |

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
# Mock 模式（不消耗 API）
USE_MOCK_LLM=true python -m tests.eval_suite.run_evaluation \
    --concurrent 1 --timeout 60 --format all --output eval_reports

# 查看报告
cat eval_reports/eval_report.json | python -m json.tool | head -40
```

______________________________________________________________________

## 🔧 故障排查

### ❌ `ModuleNotFoundError: No module named 'task_planner'`

**原因**：未安装包或用了错误的 Python 解释器。

```bash
# 用 editable 安装
pip install -e .

# 确认解释器
which python
python -c "import task_planner; print(task_planner.__file__)"
# 期望：指向 src/task_planner/__init__.py
```

### ❌ `pymongo.errors.ServerSelectionTimeoutError: task_mongodb:27017`

**原因**：`MONGO_HOST` 指向了 Docker Compose 服务名，但 Python 从宿主机跑，无法解析。

**修复**：`.env` 里改成 `MONGO_HOST=localhost`。

### ❌ `AttributeError: module 'dash.html' has no attribute 'Style'`

**原因**：Dash 4.x 移除了 `html.Style`。本项目已迁移到 `assets/custom.css`。

**修复**：确认 `src/task_planner/main/assets/custom.css` 存在，且 `dash.Dash(assets_folder="assets")`。

### ❌ `ImportError: cannot import name 'XXX' from 'task_planner.infrastructure.cog'`

**原因**：`config/schema.yaml` 里未声明该配置项，或 `cog/sections/*.py` 未注册。

**修复**：

```bash
# 1. 确认 schema 里有没有这个字段
grep -n "XXX" config/schema.yaml

# 2. 如果没有，加进去（含 purpose + scope + type）
#    或在 cog/sections/ 下对应 section 里用 ConfigEntry 声明

# 3. 检查运行时能看到哪些配置
python -c "from task_planner.infrastructure.cog import hub; print(len(hub), hub.snapshot()[0].purpose)"

# 4. 全链路 import 测试（暴露真缺的符号）
python -c "import task_planner.main.dash_app"
```

### ❌ `ValueError: X: secret 字段必须 from_env`

**原因**：在 `schema.yaml` 里声明了 `secret: true` 但没加 `from_env: true`。
这是有意设计——防止明文密钥落盘。

**修复**：

```yaml
SOME_API_KEY:
  type: str
  default: ""
  scope: developer
  purpose: 描述
  secret: true
  from_env: true        # ← 补上这一行
```

然后通过环境变量提供值（`.env` / Docker env / K8s Secret 都行）。

### ❌ pytest 卡住或极慢

**原因**：`USE_MOCK_LLM` 未生效，测试在走真实 API。

```bash
# 显式注入
USE_MOCK_LLM=true pytest tests/

# 或改 .env：
# USE_MOCK_LLM=true
```

### ❌ Dash 端口被占用

```bash
# 找进程
lsof -i :8050

# 杀掉
kill $(lsof -ti:8050)

# 或改端口
echo "DASH_PORT=8051" >> .env
```

### ❌ 浏览器打开但点击无反应

**排查**：F12 → Console 看 JS 报错；Network 看 `_dash-update-component` 请求。

**常见**：`assets/custom.css` 404（检查 `assets_folder` 路径）。

### ⚠️ 日志目录出现在 3 个地方

现象：

```
./logs/task_planner.log
./src/logs/task_planner.log
./src/task_planner/logs/task_planner.log
```

**原因**：`LOG_DIR` 是相对路径，从不同 CWD 启动会写到不同位置。

**修复**（推荐）：`.env` 里显式指定绝对路径：

```bash
LOG_DIR=/abs/path/to/project/logs
```

或在 `config/user.yaml` 里覆盖（不推荐，因为 `LOG_DIR` 是 developer scope）：

```yaml
# config/schema.yaml
base:
  LOG_DIR:
    type: str
    default: logs           # 相对项目根
    scope: developer
    purpose: 日志目录，建议用绝对路径避免歧义
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
- `pyright` 无 error
- 新增功能附带测试

______________________________________________________________________

## 🙏 致谢

本项目基于以下优秀技术构建：

- [LangGraph](https://langchain-ai.github.io/langgraph/) — 有向图状态机编排

- [Dash](https://dash.plotly.com/) / [Cytoscape](https://js.cytoscape.org/) — 交互式可视化

- [Motor](https://motor.readthedocs.io/) — 异步 MongoDB 驱动

- [AsyncOpenAI](https://github.com/openai/openai-python) / [httpx](https://www.python-httpx.org/) — 高性能异步 LLM 调用

- [pyvis](https://pyvis.readthedocs.io/) / [pydot](https://pypi.org/project/pydot/) / [cairosvg](https://cairosvg.org/) — 图渲染与导出

- [ruamel.yaml](https://pypi.org/project/ruamel.yaml/)（可选）— 配置工具保留 YAML 注释

  核心贡献者：**JiangDake**

______________________________________________________________________

## 📄 许可证

[GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0)

______________________________________________________________________

______________________________________________________________________

## ℹ️ 已知问题

### 关于 `abandon/` 目录

保留归档，不参与运行。理由：

- 出奇怪 bug 时可 diff 出「是否重构引入」
- `scripts/check_config_drift.py --old` 仍用它做基线
- `pyproject.toml` 里 `[tool.setuptools.packages.find]` 设了
  `exclude = ["task_planner.abandon*"]`，不会打包进 wheel

**不要 ignore**：`.gitignore` 里不要加 `abandon/`。

**不要 import**：`src/` 下所有代码不引用 `abandon.*`。

______________________________________________________________________

Made with ❤️ by JiangDake
