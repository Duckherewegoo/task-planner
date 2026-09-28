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
├── src/task_planner/
│   ├── core/
│   │   ├── graph/                 # LangGraph 图定义
│   │   │   ├── state.py           # TaskState 状态契约
│   │   │   ├── nodes.py           # 节点函数（intent/plan/refine/...）
│   │   │   └── workflow.py        # 图拓扑 + Checkpointer
│   │   └── database.py            # MongoDB 异步数据层
│   ├── infrastructure/
│   │   ├── config.py              # 全局配置中心
│   │   ├── llm_client.py          # LLM 异步调用封装
│   │   ├── logger_setup.py        # 异步日志系统
│   │   └── session_store.py       # 会话存储接口（内存/Redis 可插拔）
│   ├── services/
│   │   ├── agent.py               # Agent 主入口（run_task_stream）
│   │   ├── stream_manager.py      # 后台流式任务 + 节点状态管理
│   │   └── view_model.py          # 前端字段投影
│   ├── utils/
│   │   ├── context.py             # cancel_event_var 上下文
│   │   ├── cytoscape_adapter.py   # DAG → Cytoscape 元素
│   │   ├── flowchart_pro.py       # 交互式 HTML 渲染
│   │   ├── pyvis_export.py        # 静态导出（SVG/PNG/PDF/DOT）
│   │   ├── presentation_utils.py  # 展示辅助
│   │   └── good_addons.py         # 运行时增强层（可选依赖）
│   └── main/
│       ├── dash_app.py            # Dash 入口
│       └── assets/custom.css
└── tests/
    ├── test_*.py                  # 单元 + 集成测试
    └── eval_suite/                # Crucible 评估套件
        ├── crucible_eval.py
        ├── dataset_manager.py
        ├── hitl_reviewer.py
        ├── run_evaluation.py
        └── harness/
            ├── agent_harness.py
            ├── evaluators.py
            └── reporters.py
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
# ── LLM ──
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

完整配置见 `src/task_planner/infrastructure/config.py`。

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

### ❌ `ImportError: cannot import name 'XXX' from config`

**原因**：`config.py` 缺少某个常量（review 时已修复大部分）。

**修复**：

```bash
# 找到真缺的常量（用 import 而不是 grep）
python -c "import task_planner.main.dash_app"
```

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
