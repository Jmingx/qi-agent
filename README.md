# qi-agent

<p align="center">
  <a href="https://github.com/Jmingx/qi-agent"><img src="https://img.shields.io/badge/GitHub-Jmingx%2Fqi--agent-blue?style=for-the-badge&logo=github" alt="GitHub"></a>
  <a href="#"><img src="https://img.shields.io/badge/Python-3.11+-green?style=for-the-badge&logo=python" alt="Python 3.11+"></a>
  <a href="#"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
</p>

**A lightweight, plugin-based Python agent framework.** Event-driven architecture with a complete tool system, security approval, context management, and task-level evaluation.

Built as a learning project — architecture guided by [Hermes Agent](https://github.com/NousResearch/hermes-agent) patterns (stateless executors, data-carrier contexts, unified control plane).

## Features

| Capability | Description |
|------------|-------------|
| **Tool system** | Registry-based (1 file = 1 tool), auto-generated schemas, parameter validation, parallel tool execution |
| **Built-in tools** | File domain (read/write/patch/list/search/delete), shell, sandboxed Python, web search/extract (dual backend), todo, clarify, get_time |
| **Security** | Sandboxed execution (restricted Python + resource limits), 3-tier approval (auto-allow / needs-approval / hard-block), sensitive-path protection |
| **Plugin architecture** | All cross-cutting concerns pluginized (event-driven): security, approval, context management, debug logging, resource monitoring — zero core intrusion |
| **Agent architecture** | Stateless executor (Agent) + data-carrier context (AgentContext) + unified control plane (AgentManager) + executor pool (AgentPool) |
| **Context management** | Token estimation / breakdown / sliding-window trimming / sticky key-info retention |
| **Task evaluation** | Task success rate, regression baseline comparison, security adversarial tests |

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Configure API key
cp .env.example .env
# Edit .env, fill in DEEPSEEK_API_KEY

# 3. Start chatting
uv run python -m qi_agent.cli

# Debug mode (see LLM requests/responses/context usage)
uv run python -m qi_agent.cli --debug
```

## Slash Commands

| Command | Description |
|---------|-------------|
| `/clear` | Clear context (start a new conversation) |
| `/remember <content>` | Remember important info (never dropped by context trimming) |
| `/usage` | View resource usage (token accumulation) |
| `/status` | View agent status (state machine: session status + loop phase) |
| `/delegate <goal>` | Delegate a task to a subagent |
| `/exit` | Exit |

## Architecture

```
qi_agent/
├── agents/              # Executor family (pluggable — swap = add a file)
│   ├── agent.py         #   Agent executor (stateless loop)
│   ├── agent_manager.py #   AgentManager: unified control plane (run/stop/steer/poll)
│   ├── pool.py          #   AgentPool: executor lifecycle (acquire/release, concurrency)
│   ├── subagent.py      #   SubagentContext + SubagentManager
│   └── factory.py       #   build_runtime + make_agent (runtime/executor separation)
├── context/             # Data carrier (AgentContext: messages/turns/usage + state machine)
├── plugins/             # Plugins (security/approval/context/debug logging/stats)
├── tools/               # Tools (1 file = 1 tool, registry-based)
├── cli.py               # Command-line REPL
└── interaction.py       # Interaction abstraction (terminal / Web UI swappable)
```

**Three-layer architecture**: executor (agents/) → tools (capabilities, registry) → plugins (cross-cutting, event-driven). Adding capability = registering a tool or mounting a plugin — the core stays untouched.

**Key design decisions**:
- **Stateless executor + data-carrier context** — Agent holds no state; all data (messages/turns/usage) lives in AgentContext. The same context can be taken over by a new Agent instance (disconnect-resume foundation).
- **Execution ownership in Manager** — CLI calls `manager.run(context_id, input)`; it never holds an Agent. Executors live in the AgentPool (acquire → chat → release).
- **ID convention** — `ctx_` prefix = session identity (data carrier); `agt_` prefix = executor identity (observability/audit).

## License

MIT
