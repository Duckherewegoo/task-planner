# 🧠 Task Planner — 智能任务规划引擎

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Async](https://img.shields.io/badge/async-ready-green.svg)](#)

> 基于 **LangGraph** 与 **大语言模型** 的自主任务规划引擎，支持动态 DAG 编排、多模态意图识别与全流程可观测性评估。  
> **v6.0 全面异步化，高性能、可扩展、生产就绪。**

---

## ✨ 核心特性

- 🧠 **智能意图识别** — 使用 LLM 精准理解用户需求，自动判断是否需要规划
- 📊 **动态 DAG 生成** — 将复杂任务拆解为原子节点，自动构建依赖关系图
- 🔄 **流式执行与实时反馈** — 异步流式推送执行进度，前端实时更新流程图
- ✅ **节点级人工确认** — 支持用户标记完成/跳过/失败，精细控制执行流程
- 📜 **完整历史追溯** — 所有任务持久化存储，支持断点续传、重新规划
- 🎨 **交互式可视化** — 基于 Dash + Cytoscape 的流程图交互，节点状态清晰可辨
- 📤 **多格式导出** — 支持 SVG/PNG/PDF/DOT/HTML/JSON 导出，满足报告与集成需求
- 🔌 **异步优先** — 全链路异步 I/O（LLM API、数据库、流式输出），高并发下依然流畅
- 🛡 **生产级增强** — 内置重试、熔断、限流、健康检查、结构化日志、性能监控

---

## 🏗️ 架构概览

┌──────────────────────────────────────────────────────────────┐
│ 用户界面 (Dash) │
│ ┌───────────┐ ┌───────────┐ ┌───────────────────────┐ │
│ │ 新建任务 │ │ 历史记录 │ │ 节点操作面板 │ │
│ └───────────┘ └───────────┘ └───────────────────────┘ │
└───────────────────────────┬──────────────────────────────────┘
│ WebSocket / 轮询 (异步)
┌───────────────────────────▼──────────────────────────────────┐
│ Stream Manager │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ 后台异步任务 (asyncio) → 快照队列 → 前端轮询 │ │
│ └────────────────────────────────────────────────────────┘ │
└───────────────────────────┬──────────────────────────────────┘
│
┌───────────────────────────▼──────────────────────────────────┐
│ Agent (LangGraph 驱动) │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ 意图识别 → 规划生成 → 节点细化 → 入库 → 渲染 → 执行 │ │
│ │ (所有节点均为异步，支持取消信号) │ │
│ └────────────────────────────────────────────────────────┘ │
└──────────┬──────────────────────┬───────────────────────────┘
│ │
┌──────────▼──────────┐ ┌────────▼──────────────────────────┐
│ LLM Client (异步) │ │ Database (Motor 异步驱动) │
│ AsyncOpenAI + httpx │ │ MongoDB 持久化存储 │
└─────────────────────┘ └────────────────────────────────────┘


---

## 🚀 快速开始

### 环境要求
- Python 3.10 或更高版本
- MongoDB（本地或远程）
- LLM API Key（支持 OpenAI 兼容接口，如 DashScope）

### 安装

```bash
# 克隆仓库
git clone https://github.com/Duckherewegoo/task-planner.git
cd task-planner

# 安装核心依赖
pip install -e .

# 安装可选加速（推荐）
pip install -e ".[orjson]"

# 安装开发依赖
pip install -e ".[dev]"

配置

复制 .env.example 为 .env 并填写必要配置：
bash

# LLM API 配置
DASHSCOPE_API_KEY=your_api_key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# MongoDB 配置
MONGO_HOST=localhost
MONGO_PORT=27017
MONGO_DB=task_planner_db

# 调试模式
DEBUG=true
USE_MOCK_LLM=false   # 开发时可设为 true 跳过真实 API 调用

启动
bash

# 启动 Dash 交互界面
task-planner

# 或通过模块启动
python -m task_planner.main.dash_app

浏览器访问 http://localhost:7860 即可使用。
🎮 使用示例
1. 新建任务

在输入框中键入需求，例如：

    “教我做甜口西红柿炒鸡蛋，需要详细步骤”

点击 开始规划，系统将：

    识别意图（是否属于可规划任务）

    生成 DAG 流程图（节点与依赖关系）

    细化每个节点的具体操作细节

    渲染并展示流程图

2. 节点操作

    点击流程图中的节点，右侧详情面板显示该节点的前置条件、操作细节、后置条件

    选择 开始执行 运行根节点，执行完成后会进入 中断状态 等待用户确认

    用户可对单个节点进行 完成 / 跳过 / 失败 操作

    系统自动根据依赖关系解锁下游节点（done 解锁 hard 依赖，failed 仅解锁 soft 边）

3. 历史任务管理

    切换到 历史记录 标签页，查看所有已保存的任务

    点击任一任务可查看完整的流程图与节点详情

    支持：

        继续执行：从中断点恢复执行

        重新规划：基于原始需求重新生成流程图

        修改规划：微调任务后重新规划

        重试失败节点：自动重试标记为失败的节点

        批量删除：清除不需要的历史数据

4. 数据导出

    在任意流程图中，使用顶部工具栏导出：

        图像：PNG / SVG

        数据：JSON / DOT / HTML（自包含流程图）

⚙️ 配置说明

环境变量（.env 或系统环境）：
变量	说明	默认值
DASHSCOPE_API_KEY	LLM API Key	必填
DASHSCOPE_BASE_URL	API Base URL	https://dashscope.aliyuncs.com/compatible-mode/v1
MONGO_HOST	MongoDB 主机	localhost
MONGO_PORT	MongoDB 端口	27017
MONGO_DB	数据库名	task_planner_db
DEBUG	调试模式	true
USE_MOCK_LLM	跳过真实 LLM 调用（测试用）	false
LLM_MAX_CONCURRENT	最大并发 LLM 请求数	10
LLM_TIMEOUT	LLM 请求超时（秒）	120
LLM_MAX_RETRIES	重试次数	3

更多配置详见 src/task_planner/infrastructure/config.py。
🧪 开发与测试
bash

# 安装开发依赖
pip install -e ".[dev]"

# 运行测试（确保 MongoDB 可用）
pytest

# 仅运行非集成测试
pytest -m "not integration"

# 代码格式化
black src/ tests/
ruff check --fix src/ tests/

# 类型检查
pyright

🤝 贡献

欢迎提交 Issue 和 Pull Request。请确保：

    代码通过 pytest 和 pyright

    遵循 PEP 8 和 black 格式化

    为新增功能编写测试

🙏 致谢

本项目基于以下优秀技术构建：

    LangGraph — 有向图状态机编排

    Dash / Cytoscape — 交互式可视化

    Motor — 异步 MongoDB 驱动

    AsyncOpenAI / httpx — 高性能异步 LLM 调用

核心贡献者：

    JiangDake

    deepseek（基于 qwen & yuanbao）

📄 许可证

本项目使用 GNU General Public License v3.0 许可。
📚 相关链接

    LangGraph 文档

    Dash 文档

    Motor 异步 MongoDB 驱动

Made with ❤️ by JiangDake, deepseek & Contributors

---
