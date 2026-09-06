# qi-agent

<p align="center">
  <a href="https://github.com/Jmingx/qi-agent"><img src="https://img.shields.io/badge/GitHub-Jmingx%2Fqi--agent-blue?style=for-the-badge&logo=github" alt="GitHub"></a>
  <a href="#"><img src="https://img.shields.io/badge/Python-3.11+-green?style=for-the-badge&logo=python" alt="Python 3.11+"></a>
  <a href="#"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-blue?style=for-the-badge" alt="English"></a>
</p>

**一个轻量级、插件化的 Python Agent 框架。** 基于事件驱动架构，提供完整的工具系统、安全审批、上下文管理与任务级评测能力。

## 特性

| 能力 | 说明 |
|------|------|
| **工具系统** | 注册制架构（1 文件 1 工具），schema 自动生成、参数校验、多工具并行执行 |
| **内置工具** | 文件域（read/write/patch/list/search/delete）、shell、沙箱 Python、web search/extract（双后端）、todo、clarify、get_time |
| **安全体系** | 沙箱执行（受限 Python + 资源限制）、三档审批（自动放行 / 需审批 / 红线硬拒）、敏感路径保护 |
| **插件架构** | 横切关注点全部插件化（事件驱动）：安全、审批、上下文管理、调试日志、资源监控——核心零侵入 |
| **Agent 架构** | 无状态执行者（Agent）+ 数据载体 context（AgentContext）+ 统一控制面（AgentManager）+ 执行者池（AgentPool） |
| **上下文管理** | token 估算 / 构成分解 / 滑动窗口裁剪 / sticky 关键信息保留 |
| **持久化记忆** | 情景 / 语义 / 程序性记忆、主动提炼、跨会话注入、上下文压缩 |
| **邮局式通信** | Message / Dispatcher / Mailbox / Transport 组成的多 Agent 消息链路，支持本地、JSON-RPC 和 socket 传输 |
| **任务级评测** | L1-L4 协议、边界、事实保持和质量评测，LLM-as-judge、rubric、历史趋势、token 与成本追踪 |
| **多种外壳** | JSON-RPC Gateway、React + Vite Web Shell；核心 Agent 与外壳解耦 |

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置 API key
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

```

## 评测

```bash
# 查看可用的 suite（不传参数不会启动评测）
uv run python -m evaluation.run

# 执行一个 JSONL 测评集（真实 LLM）
uv run python -m evaluation.run --suite smoke
uv run python -m evaluation.run --suite regression
uv run python -m evaluation.run --suite long_context
uv run python -m evaluation.run --suite subagent

# 执行全部 JSONL 测评集
uv run python -m evaluation.run --suite all

# 只调试一个用例
uv run python -m evaluation.run --suite smoke --case-id time_tool
uv run python -m evaluation.run --case-id time_tool
```

`--suite` 的可选值来自 `evaluation/suites/*.jsonl` 文件名；新增 JSONL 文件即可
新增测评集，不需要修改 Python 评测代码。`--case-id` 可以把调试范围缩小到单个用例，
用例不存在或 ID 重复时会直接报错，不会启动 Gateway。

所有测评都经过隔离的评测 Gateway，并统一写入 Opik 项目
`qi-agent-evaluation`：每个 suite 一个 Dataset，每次运行一个 Experiment，每个
用例一条 Trace。终端会输出 Dataset、Experiment、Opik Trace ID 和 Jaeger URL，
方便从 Opik 或 Jaeger 反向定位具体用例。每次运行还会归档到 `eval_runs/`，记录
通过率、token、耗时、成本和回归对比。

## Web Shell（浏览器界面）

```bash
# 终端 1——启动内核 serve 进程（WebSocket 端口 8765）
uv run python -m qi_agent.serve --port 8765

# 终端 2——启动 web 应用（FastAPI，端口 9000）
uv run python -m qi_agent.web.server --port 9000

# 浏览器打开 http://127.0.0.1:9000
```

> web 应用与内核是**独立进程**——浏览器连 web 的 WebSocket，web 转发 JSON-RPC
> 到内核 serve 进程（Hermes 式 serve 架构）。web 只通过 RPC 调内核——不直接
> import 核心模块。

### 前端开发（HMR）

```bash
cd qi_agent/web/frontend
npm install
npm run dev        # Vite dev server http://127.0.0.1:5173（热更新）
npm run build      # 生产构建 → dist/（web 应用自动托管）
```

## RPC 方法（Gateway——Web/任意外壳统一调用）

| 方法 | 说明 |
|------|------|
| `session/create` | 新建会话 |
| `session/resume` | 恢复会话 |
| `session/list` | 会话列表（活跃 + 历史） |
| `session/status` | 查询会话状态 + 结果（轮询） |
| `session/stop` | 停止当前任务 |
| `session/delegate` | 拉起子 agent |
| `message/send` | 发消息（流式回调） |
| `approval/respond` | 审批响应 |
| `context/info` | 查看上下文构成 |
| `context/compact` | 手动压缩 |
| `memory/get` | 查看跨会话记忆 |
| `memory/save` | 保存记忆条目 |

## 架构

```
qi_agent/
├── agents/              # 执行者家族（可插拔——换执行者=加文件）
│   ├── agent.py         #   Agent 执行者（无状态循环）
│   ├── agent_manager.py #   AgentManager：统一控制面（run/stop/steer/poll）
│   ├── pool.py          #   AgentPool：执行者生命周期（acquire/release，并发治理）
│   ├── subagent.py      #   SubagentContext + SubagentManager
│   └── factory.py       #   build_runtime + make_agent（运行时/执行者分离）
├── context/             # 数据载体（AgentContext：消息/轮数/用量 + 状态机）
├── plugins/             # 插件（安全/审批/上下文/调试日志/统计）
├── tools/               # 工具（1 文件 1 工具，注册制）
├── gateway/             # JSON-RPC 网关（会话/消息/审批方法）
├── serve.py             # 内核 serve 进程（WebSocket 传输——Hermes 式）
├── web/                 # Web Shell（FastAPI + React 前端——独立进程）
│   ├── server.py        #   FastAPI 应用 + WS 端点 + Bridge（只调 serve RPC）
│   └── frontend/        #   React + Vite SPA（聊天/侧边栏/审批弹窗）
└── interaction.py       # 交互抽象层（Web UI 可替换）
evaluation/              # 任务级评测平台（runner / judge / history / trends）
```

**三层架构**：执行者（agents/）→ 工具（能力单元，注册制）→ 插件（横切关注点，事件驱动）。新增能力只需注册工具或装配插件，核心零改动。

**关键设计决策**：
- **无状态执行者 + 数据载体 context**——Agent 不持有状态，所有数据（消息/轮数/用量）在 AgentContext。同一 context 可被新 Agent 实例接管（断线续聊基础）。
- **执行权归还 Manager**——应用层只调 `manager.run(context_id, input)`，不持有 Agent。执行者在 AgentPool 内即用即弃（acquire → chat → release）。
- **ID 约定**——`ctx_` 前缀 = 会话身份（数据载体）；`agt_` 前缀 = 执行者身份（可观测/审计）。

## 设计理念

1. **在构建中学习。** 循环、工具、安全、记忆和评测机制都从零实现，确保每个
   机制都能被理解和追踪。
2. **保持核心窄而稳定。** 核心保持小型、事件驱动；能力通过工具和插件扩展，
   而不是不断膨胀内核。
3. **有意识地裁剪，并记录原因。** 多租户、分布式执行、向量数据库等考虑过但
   暂未采用的能力，都有明确的设计理由。
4. **通过设计实现并发，而不是依赖锁。** 尽量减少共享状态，消息通过队列流转，
   所有权边界清晰。

## 路线图

- **Skill 系统**——程序性记忆：`SKILL.md`、索引注入和按需加载
- **可观测性**——OpenTelemetry 导出（一次对话 = 一条可回放 Trace）
- **Prompt 缓存**——字节稳定的系统 Prompt 分区（L0/L1/L2）
- **向量检索**——记忆规模需要时启用；接口已经抽象完成

## 许可证

MIT
