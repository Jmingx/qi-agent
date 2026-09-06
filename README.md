# qi-agent

<p align="center">
  <a href="https://github.com/Jmingx/qi-agent"><img src="https://img.shields.io/badge/GitHub-Jmingx%2Fqi--agent-blue?style=for-the-badge&logo=github" alt="GitHub"></a>
  <a href="#"><img src="https://img.shields.io/badge/Python-3.11+-green?style=for-the-badge&logo=python" alt="Python 3.11+"></a>
  <a href="#"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
</p>

**A lightweight, plugin-based Python agent framework.** Event-driven architecture with a complete tool system, security approval, context management, and task-level evaluation.

## Features

## ✨ Highlights

| | |
|---|---|
| 🧱 **From scratch, zero framework** | Agent loop, tool system, security chain, memory, evaluation — all hand-built. The best way to actually *understand* agents. |
| 🛡️ **Defense-in-depth security** | Sandboxed code execution (restricted Python + resource limits), 3-tier approval (auto / approve / hard-block), sensitive-path redlining, approval-bypass audit. Security posture exceeds most commercial frameworks. |
| 📮 **Postal-system communication** | Multi-agent messaging modeled on the postal service: **Message** (id/sender/target), **Dispatcher** (post office — routing/audit), **Mailbox** (stateless inbox), **Transport** (swappable backend: local → JSON-RPC → socket). Concurrency-safe by design — communication through queues, not shared state. |
| 🧠 **Persistent memory** | Three-layer memory (episodic/semantic/procedural), proactive distillation (the agent writes what it learned), cross-session injection, context compression with strategy chain. |
| 📊 **Evaluation platform** | Task-level evaluation (L1–L4: protocol/boundedness/fact-retention/quality), LLM-as-judge scoring with rubrics, full history retention, trend analysis, token & cost tracking. |
| 🔌 **Everything is a plugin** | Security, approval, context management, memory, observability — all cross-cutting concerns mount as event-driven plugins. Zero core intrusion. |
| 🌐 **Multiple shells** | JSON-RPC gateway and Web Shell (React + Vite). Agent core stays untouched — shells are consumers. |

## Architecture

```
┌─ Shells ─────────────────────────────────────────────┐
│  JSON-RPC Gateway │ Web Shell (React+Vite)             │
└──────────────────────┬───────────────────────────────┘
┌─ Agent Kernel ───────▼───────────────────────────────┐
│  AgentManager (control plane: run/stop/steer/poll)    │
│  AgentPool (executor lifecycle + concurrency)         │
│  Agent (stateless executor — disposable)              │
│  AgentContext (data carrier: messages/turns/usage)    │
│  AgentMailbox (postal system: Message/Dispatcher)     │
├──────────────────────────────────────────────────────┤
│  Tools (registry, 1 file = 1 tool) │ Plugins (events) │
│  Security chain │ Context management │ Memory store    │
└──────────────────────────────────────────────────────┘
```

**Key design decisions:**

- **Stateless executor + data-carrier context.** The Agent holds no state; everything lives in AgentContext. A new executor can take over the same context — the foundation for disconnect/resume.
- **Execution ownership in the Manager.** The application layer calls `manager.run()`, never holds an Agent. Executors live in the pool: acquire → chat → release.
- **Communication through queues, not shared state.** Multi-agent messaging is a postal system — concurrency problems are designed out, not locked out.
- **Safety over convenience.** Approval gates on every dangerous action, sandboxed code, red-lines that even approval cannot bypass.

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Configure API key
cp .env.example .env
# Edit .env, fill in DEEPSEEK_API_KEY

```

## Evaluation

```bash
# Show available suites
uv run python -m evaluation.run

# Run one JSONL suite (real LLM)
uv run python -m evaluation.run --suite smoke

# Other suites use the same Gateway runner
uv run python -m evaluation.run --suite regression
uv run python -m evaluation.run --suite long_context
uv run python -m evaluation.run --suite subagent

# Run every JSONL suite
uv run python -m evaluation.run --suite all

# Run one case for debugging
uv run python -m evaluation.run --case-id time_tool
```

Every run is archived forever (`eval_runs/`), scored (LLM-as-judge with per-task rubrics), cost-tracked, and trended — regressions are caught by sliding-window comparison, not guesswork.

Suite names are discovered from `evaluation/suites/*.jsonl`; adding a JSONL file adds a
new suite without changing Python evaluation code. `--suite all` runs every suite. For focused
debugging, use `--case-id`:

```bash
uv run python -m evaluation.run --suite smoke --case-id time_tool
uv run python -m evaluation.run --case-id time_tool
```

Each run uses the isolated evaluation Gateway and records its results in the Opik
project `qi-agent-evaluation`: one Dataset per suite, one Experiment per run, and one
Trace per case. The console prints the Dataset, Experiment, Opik trace ID, and Jaeger
URL so a case can be located from either system.

## Web Shell

```bash
# Terminal 1 — start the kernel serve process (WebSocket port 8765)
uv run python -m qi_agent.serve --port 8765

# Terminal 2 — start the web application (FastAPI, port 9000)
uv run python -m qi_agent.web.server --port 9000

# Open http://127.0.0.1:9000 in a browser
```

The web application and kernel run as **separate processes**. The browser connects to
the web WebSocket, and the web server forwards JSON-RPC requests to the kernel serve
process; the web layer does not import the core directly.

### Frontend development (HMR)

```bash
cd qi_agent/web/frontend
npm install
npm run dev        # Vite dev server at http://127.0.0.1:5173
npm run build      # production build -> dist/
```

## RPC Methods

| Method | Description |
|--------|-------------|
| `session/create` | Create a session |
| `session/resume` | Resume a session |
| `session/list` | List active and historical sessions |
| `session/status` | Query session state and result |
| `session/stop` | Stop the current task |
| `session/delegate` | Start a subagent |
| `message/send` | Send a message with streaming callbacks |
| `approval/respond` | Respond to an approval request |
| `context/info` | Inspect context composition |
| `context/compact` | Trigger context compression |
| `memory/get` | Read cross-session memory |
| `memory/save` | Save a memory entry |

## Architecture

```
qi_agent/
├── agents/          # Executor family (Agent/Manager/Pool/Mailbox/Factory)
├── context/         # Data carrier + compression strategy chain
├── plugins/         # Event-driven plugins (security/approval/memory/...)
├── tools/           # Tools (1 file = 1 tool, registry-based)
├── gateway/         # JSON-RPC gateway
├── serve.py         # Kernel serve process (WebSocket)
├── web/             # Web Shell (FastAPI + React — separate process)
└── interaction.py   # Pluggable interaction abstraction
evaluation/          # Task evaluation platform (runner/judge/history/trends)
```

**Three-layer architecture**: executor (agents/) → tools (capabilities, registry) → plugins (cross-cutting, event-driven). Adding capability = registering a tool or mounting a plugin — the core stays untouched.

**Key design decisions**:
- **Stateless executor + data-carrier context** — Agent holds no state; all data (messages/turns/usage) lives in AgentContext. The same context can be taken over by a new Agent instance (disconnect-resume foundation).
- **Execution ownership in Manager** — CLI calls `manager.run(context_id, input)`; it never holds an Agent. Executors live in the AgentPool (acquire → chat → release).
- **ID convention** — `ctx_` prefix = session identity (data carrier); `agt_` prefix = executor identity (observability/audit).

## License

MIT
