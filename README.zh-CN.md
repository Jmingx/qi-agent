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
| **任务级评测** | 任务成功率评测、回归基线对比、安全对抗测试 |

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置 API key
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 3. 启动对话
uv run python -m qi_agent.cli

# 调试模式（查看 LLM 请求/响应/上下文占用）
uv run python -m qi_agent.cli --debug
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `/clear` | 清理上下文（开始新对话） |
| `/remember <内容>` | 记录重要信息（上下文裁剪时永不丢弃） |
| `/usage` | 查看资源消耗（token 累计） |
| `/status` | 查看 agent 状态（两级状态机：会话级 + 循环级） |
| `/delegate <目标>` | 委派任务给 subagent |
| `/exit` | 退出 |

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
├── cli.py               # 命令行 REPL
└── interaction.py       # 交互抽象层（终端 / Web UI 可替换）
```

**三层架构**：执行者（agents/）→ 工具（能力单元，注册制）→ 插件（横切关注点，事件驱动）。新增能力只需注册工具或装配插件，核心零改动。

**关键设计决策**：
- **无状态执行者 + 数据载体 context**——Agent 不持有状态，所有数据（消息/轮数/用量）在 AgentContext。同一 context 可被新 Agent 实例接管（断线续聊基础）。
- **执行权归还 Manager**——CLI 只调 `manager.run(context_id, input)`，不持有 Agent。执行者在 AgentPool 内即用即弃（acquire → chat → release）。
- **ID 约定**——`ctx_` 前缀 = 会话身份（数据载体）；`agt_` 前缀 = 执行者身份（可观测/审计）。

## 许可证

MIT
