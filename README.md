# 🧠 Task Planner — 智能任务规划引擎

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Async](https://img.shields.io/badge/async-ready-green.svg)](#)

> 基于 **LangGraph** 与 **大语言模型** 的自主任务规划引擎，支持动态 DAG 编排、多模态意图识别与全流程可观测性评估。
> **全链路异步化，高性能、可扩展。**

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
```

______________________________________________________________________

## 📁 项目结构

```
new_task_planner/
├── config/                        # 🔴 配置数据（外部，可挂载）
│   ├── schema.yaml                # 开发者声明（值 + purpose + scope + secret）
│   ├── schema.yaml.example        # 模板示例
│   ├── user.yaml                  # 用户可覆盖项（仅 scope=user 生效）
│   └── user.yaml.example          # 模板示例
│
├── data/                          # 🔴 数据（外部）
│   ├── blocked_words/             # 违禁词库（一行一词，扁平/嵌套均可）
│   │   ├── 反动词库.txt
│   │   ├── 暴恐词库.txt
│   │   ├── 色情词库.txt
│   │   ├── 涉枪涉爆.txt
│   │   ├── 广告类型.txt
│   │   ├── 非法网址.txt
│   │   ├── 零时-Tencent.txt
│   │   └── ...（共 17 个词库文件）
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
│   ├── abandon/                   # 🗄️ 归档（旧实现，不参与运行）
│   │   ├── config_old.py          # 旧大宗 config.py
│   │   ├── agent_legacy.py / agent_nah.py
│   │   ├── dash_app_legacy.py
│   │   ├── llm_client_legacy.py
│   │   ├── stream_manager_legacy.py
│   │   ├── datebase_legacy.py     # 注意拼写：datebase
│   │   ├── good_addons_old.py
│   │   └── _nodes_legacy.py
│   │
│   ├── core/
│   │   ├── db/                    # MongoDB 数据层（拆分包）
│   │   │   ├── client.py          # 连接生命周期 + 索引
│   │   │   ├── schema.py          # 数据清洗 / 状态辅助 / 常量
│   │   │   ├── plans.py           # Plan CRUD
│   │   │   ├── tasks.py           # Task CRUD + 状态更新
│   │   │   ├── nodes.py           # nodes 数组操作
│   │   │   └── manager.py         # DBManager 兼容类
│   │   ├── graph/
│   │   │   ├── state.py           # TaskState 状态契约
│   │   │   ├── nodes/             # 图节点（拆分包）
│   │   │   │   ├── intent.py / plan.py / refine.py
│   │   │   │   ├── save.py / render.py / execute.py
│   │   │   │   ├── direct.py / cancel.py
│   │   │   │   ├── routes.py      # 路由函数（纯计算）
│   │   │   │   ├── sanitize.py    # 横切：脱敏 / 视图过滤
│   │   │   │   └── _executor.py   # 单节点执行器
│   │   │   └── workflow.py        # 图拓扑 + Checkpointer
│   │   └── database.py            # 【兼容壳】转发到 core/db
│   │
│   ├── infrastructure/
│   │   ├── cog/                   # 配置中枢（拆分包）
│   │   │   ├── entry.py / store.py / guard.py / loader.py
│   │   │   ├── session.py / section.py / registry.py / hub.py
│   │   │   └── sections/          # base / dashscope / llm / mongo
│   │   │                          # render / runtime / security / web
│   │   ├── llm/                   # LLM 调用（拆分包）
│   │   │   ├── client.py / core.py / api.py
│   │   │   ├── json_utils.py / validator.py / mocks.py
│   │   │   └── errors.py
│   │   ├── prompts/               # Prompt 资源
│   │   │   ├── loader.py          # 加载 .txt → Template
│   │   │   ├── intent.txt / planner.txt
│   │   │   └── node_refine.txt / execute_node.txt
│   │   ├── assets/                # Cytoscape 样式 + JS 模板
│   │   │   ├── cytoscape_styles.py
│   │   │   └── cytoscape_js.py
│   │   ├── constants.py           # 代码常量 / 枚举
│   │   ├── regexes.py             # 预编译正则
│   │   ├── ui_styles.py           # Dash/Gradio 内联样式
│   │   ├── blocked_words.py       # 违禁词库加载 + AC自动机匹配
│   │   ├── logger_setup.py        # 异步日志系统
│   │   ├── session_store.py       # 会话存储（内存/Redis 可插拔）
│   │   └── llm_client.py          # 【兼容壳】转发到 infrastructure/llm
│   │
│   ├── services/
│   │   ├── agent/                 # Agent 主入口（拆分包）
│   │   │   ├── session.py / state.py
│   │   │   ├── stream.py / commands.py
│   │   │   └── view.py
│   │   ├── stream/                # 流式任务管理（拆分包）
│   │   │   ├── state.py / cleaner.py
│   │   │   ├── launcher.py / control.py
│   │   │   └── view.py
│   │   ├── agent.py               # 【兼容壳】转发到 services/agent
│   │   ├── stream_manager.py      # 【兼容壳】转发到 services/stream
│   │   └── view_model.py          # 前端字段投影
│   │
│   ├── utils/                     # 独立工具（不拆，KISS 加固）
│   │   ├── context.py             # cancel_event 上下文
│   │   ├── cytoscape_adapter.py   # DAG → Cytoscape 元素
│   │   ├── flowchart_pro.py       # 交互式 HTML 渲染
│   │   ├── pyvis_export.py        # 静态导出（SVG/PNG/PDF/DOT）
│   │   ├── presentation_utils.py  # 展示辅助
│   │   └── good_addons.py         # 运行时增强层（drop-in）
│   │
│   ├── main/
│   │   ├── ui/                    # Dash UI（拆分包）
│   │   │   ├── app.py / layout.py
│   │   │   ├── constants.py / data_ops.py / export.py
│   │   │   └── callbacks/         # 分组回调
│   │   │       ├── new_task.py    # 新建任务
│   │   │       ├── history.py     # 历史记录
│   │   │       ├── export_cb.py   # 导出
│   │   │       └── graph_js.py    # 客户端回调
│   │   ├── dash_app.py            # 【兼容壳】转发到 main/ui
│   │   ├── assets/custom.css
│   │   └── py.typed
│   │
│   └── logs/                      # ⚠️ 见下方"已知问题"
│
├── tests/
│   ├── test_package/              # 单元 + 集成测试
│   │   ├── test_config_contract.py
│   │   ├── test_evaluators.py
│   │   ├── test_graph_finished.py
│   │   ├── test_initial_state.py
│   │   ├── test_json_extraction.py
│   │   ├── test_normalize_output.py
│   │   └── test_session_store.py
│   └── eval_suite/                # Crucible 评估套件
│       ├── crucible_eval.py
│       ├── dataset_manager.py
│       ├── hitl_reviewer.py
│       ├── run_evaluation.py
│       └── harness/
│           ├── agent_harness.py
│           ├── evaluators.py
│           └── reporters.py
│
├── logs/                          # 运行日志（项目根）
├── eval_reports/                  # 评估报告输出
├── docker-compose.yml
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

  核心贡献者：**JiangDake**

______________________________________________________________________

## 📄 许可证

[GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0)

______________________________________________________________________

Made with ❤️ by JiangDake
