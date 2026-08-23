# qi-agent 从零构建计划（类 Hermes Agent 练手项目）

> **Goal:** 从零构建一个类似 Hermes 的 AI Agent（Python + DeepSeek API），采用"最小闭环 → 打磨核心 → 扩展功能"的渐进路线，作为练手项目。

**Architecture:** 参考本地 Hermes 安装（`C:\Users\xie\PycharmProjects\hermes-agent`）的模块划分：CLI 入口 / 配置层 / LLM provider 封装 / Agent 循环 / 工具注册系统 / 会话存储。每个阶段都有可运行的交付物，先跑通再扩展。

**Tech Stack:** Python 3.11 + uv 项目管理、DeepSeek API（OpenAI 兼容协议，用户已有 key）、SQLite（会话持久化）、YAML（配置）、pytest（测试）。

**当前状态（已验证 2026-08-14）:**
- `qi-agent` 目录为空，未初始化 git
- Python 3.11.15 + uv 0.11.12 可用
- 用户有 DeepSeek API key（此前存放在 deepseek-harness repo 的 .env）
- 主模型：deepseek-v4-flash（文本模型，OpenAI 兼容接口）

---

## 总路线图（五阶段）

```
阶段 0  脚手架        → 项目骨架能跑
阶段 1  最小对话闭环   → 命令行里能和 agent 聊天 ★第一个里程碑
阶段 2  工具调用      → agent 能自己用工具完成任务 ★核心能力
阶段 3  记忆与会话     → 重启后还记得聊过什么
阶段 4  配置与扩展     → 换模型、加功能不用改代码
阶段 5  进阶方向       → 技能系统 / 定时任务 / 消息平台 / MCP
```

每个阶段结束 = 有一个**能跑起来**的东西 + 通过验收检查。阶段 1-4 建议严格顺序，阶段 5 按兴趣自由选。

---

## 阶段 0：项目脚手架

**目标:** 建立标准 Python 项目结构，任何一步都能验证。

**Files:**
- Create: `pyproject.toml`（uv 管理，包名 `qi-agent`，模块 `qi_agent/`）
- Create: `qi_agent/__init__.py`、`qi_agent/cli.py`
- Create: `tests/test_smoke.py`
- Create: `.gitignore`（含 `.venv/`、`.env`、`*.db`）
- Create: `.env.example`（`DEEPSEEK_API_KEY=` 占位）

**Step 1:** `git init` + 初始 commit
**Step 2:** `uv init` 或手写 pyproject.toml，`uv add openai python-dotenv pyyaml`
**Step 3:** 写 smoke test，`uv run pytest` 通过
**Step 4:** 验证：`uv run python -c "import qi_agent; print('ok')"` 输出 ok

**验收:** `uv run pytest` 全绿；git 仓库可用。

---

## 阶段 1：最小对话闭环（★第一个里程碑）

**目标:** 命令行里能跟 agent 连续对话。这是整个项目的心脏——一切后续功能都挂在这个循环上。

**Files:**
- Create: `qi_agent/config.py` — 读 `.env` 拿 API key、读配置
- Create: `qi_agent/llm.py` — DeepSeek API 客户端封装（openai SDK，base_url 指向 DeepSeek）
- Create: `qi_agent/agent.py` — 对话循环核心
- Modify: `qi_agent/cli.py` — REPL 入口（输入 → 回复 → 再输入）

**核心设计（agent.py 的最小循环）:**

```python
class Agent:
    def __init__(self, client, system_prompt="你是一个有用的助手。"):
        self.client = client
        self.messages = [{"role": "system", "content": system_prompt}]

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        reply = self.client.chat(self.messages)      # 调用 LLM
        self.messages.append({"role": "assistant", "content": reply})
        return reply
```

**Step 1（TDD）:** 写 `tests/test_agent.py`——mock LLM 客户端，验证消息历史追加顺序（user → assistant 交替）
**Step 2:** 实现 `llm.py`：用 `openai.OpenAI(base_url="https://api.deepseek.com", api_key=...)`，封装 `chat(messages) -> str`
**Step 3:** 实现 `agent.py` 和 `cli.py` REPL（`exit` / `quit` 退出）
**Step 4:** 验收——终端真实对话 3 轮，确认多轮上下文生效（问"我叫小明"→ 再问"我叫什么"能答出）

**验收:** CLI 连续对话上下文连贯；`uv run pytest` 通过。

---

## 阶段 2：工具调用（★核心能力，agent 的灵魂）

**目标:** agent 能自主决定调用工具（函数），把结果拿回来继续推理，直到完成任务。这就是"Agent"和"聊天机器人"的分水岭。

**Files:**
- Create: `qi_agent/tools.py` — 工具注册机制
- Create: `qi_agent/tools/builtin.py` — 内置工具（get_time、read_file、write_file、shell）
- Modify: `qi_agent/agent.py` — 升级为 tool-calling agent loop
- Create: `tests/test_tools.py`

**核心设计：**

```python
# tools.py — 装饰器注册机制
_TOOL_REGISTRY = {}

def tool(name=None, description=""):
    """装饰器：把函数注册成可被 LLM 调用的工具"""
    def decorator(fn):
        _TOOL_REGISTRY[name or fn.__name__] = {
            "fn": fn,
            "description": description,
            "schema": build_schema(fn),   # 从函数签名/注解生成 JSON Schema
        }
        return fn
    return decorator

def get_tool_schemas() -> list: ...   # 给 LLM 的 tools 参数
def execute_tool(name: str, args: dict) -> str: ...  # 执行并返回字符串结果
```

**Agent loop（agent.py 核心）：**

```python
def run(self, user_input: str, max_turns: int = 8) -> str:
    self.messages.append({"role": "user", "content": user_input})
    for _ in range(max_turns):
        resp = self.client.chat_with_tools(self.messages, get_tool_schemas())
        if resp.tool_calls:                       # LLM 想调用工具
            self.messages.append(resp.assistant_msg)
            for call in resp.tool_calls:
                result = execute_tool(call.name, call.arguments)
                self.messages.append({"role": "tool", "tool_call_id": call.id,
                                      "content": result})
        else:                                     # LLM 直接给出最终回答
            self.messages.append(resp.assistant_msg)
            return resp.content
    return "已达最大轮数，任务可能未完成"
```

**Step 1（TDD）:** mock 一个返回 tool_calls 的响应 → 断言工具被执行、结果回填、循环继续
**Step 2:** 实现 `tools.py` 注册机制 + schema 生成
**Step 3:** 写 3 个内置工具：`get_time`（无参数）、`read_file`（路径参数）、`shell`（命令参数，⚠️ 注意安全提示）
**Step 4:** 升级 `agent.py` 为 loop；验证 max_turns 上限
**Step 5:** 验收——真实对话："现在几点了？"→ agent 调 get_time 回答；"读一下 xxx 文件"→ 能读

**验收:** agent 能自主完成至少 2 个不同工具的调用链；超轮数有明确提示。

---

## 阶段 3：记忆与会话管理

**目标:** 对话历史持久化，重启 CLI 能恢复上次会话；支持多会话切换。参照 Hermes 的 session 概念。

**Files:**
- Create: `qi_agent/storage.py` — SQLite 封装（sessions / messages 两张表）
- Create: `qi_agent/session.py` — Session 对象（加载/保存历史）
- Modify: `qi_agent/cli.py` — 加 `--session <id>` / `--new` 参数；启动时列出历史会话
- Create: `tests/test_storage.py`

**核心设计（storage.py 表结构）：**

```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,                 -- 会话标题（可用首条消息自动生成）
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    role TEXT,                  -- system / user / assistant / tool
    content TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**Step 1（TDD）:** 测试 create_session / append_message / load_messages 往返
**Step 2:** 实现 storage.py（`sqlite3` 标准库即可，无需 ORM）
**Step 3:** Agent 启动时从 Session 加载历史；每轮对话自动保存
**Step 4:** CLI 支持列出会话、恢复指定会话
**Step 5:** 验收——退出重进，`--session 1` 恢复后继续问"我刚才叫什么名字"能答出

**验收:** 会话数据在 `qi_agent.db` 中可见；重启恢复成功。

---

## 阶段 4：配置与可扩展性

**目标:** 把可变参数全部外置到 YAML 配置，实现多 provider 支持。参照 Hermes 的 `config.yaml` 思路。

**Files:**
- Create: `qi_agent/config.yaml` — 默认配置（模型、温度、系统提示词、max_turns）
- Create: `qi_agent/config.py` — YAML 加载 + 环境变量覆盖（key 不进配置文件）
- Modify: `qi_agent/llm.py` — provider 抽象（DeepSeek / OpenAI / 本地 Ollama）
- Create: `tests/test_config.py`

**核心设计（config.yaml）：**

```yaml
model:
  provider: deepseek        # deepseek | openai | ollama
  name: deepseek-v4-flash
  temperature: 0.7
  base_url: https://api.deepseek.com

agent:
  system_prompt: "你是一个有用的助手。"
  max_turns: 8
  history_limit: 20        # 超过后裁剪旧消息（上下文管理）

storage:
  db_path: ./qi_agent.db
```

**Step 1:** 实现 config 加载（YAML + `.env` 中 `DEEPSEEK_API_KEY` 注入）
**Step 2:** provider 抽象——`llm.py` 根据配置选择客户端；Ollama 走同一 OpenAI 兼容协议（base_url 换成 `http://localhost:11434/v1`）
**Step 3:** 上下文管理——历史超过 `history_limit` 时裁剪，可配置
**Step 4:** 验收——改 yaml 切到 ollama（如有本地模型）或换模型名，无需改代码

**验收:** 修改 config.yaml 的模型名/温度即时生效；key 始终只在 .env。

---

## 阶段 5：进阶方向（按兴趣选择，每个独立可做）

### 5a. 技能系统（参照 Hermes skills）
- `qi_agent/skills/` 目录下 SKILL.md 格式的技能文件，按需加载注入 system prompt
- 练手点：文件扫描、frontmatter 解析、触发匹配

### 5b. 流式输出
- SSE 流式打字机效果，`llm.py` 加 `stream=True` 分支
- 练手点：生成器/回调、中断处理

### 5c. 定时任务（参照 Hermes cron）
- 后台调度器：`schedule` 库 + 任务表，定时触发 agent 执行任务
- 练手点：进程模型（常驻 vs 每次新起）、任务持久化

### 5d. 消息平台适配（参照 Hermes gateway）
- 抽象 PlatformAdapter 接口，先接 Telegram bot 或微信
- 练手点：适配器模式、异步事件循环、并发会话

### 5e. MCP 工具生态
- 接入 MCP server，让 agent 能用外部工具（文件系统、浏览器等）
- 练手点：协议对接、JSON-RPC

---

## 推荐练习节奏

| 阶段 | 预计时间 | 里程碑 |
|------|---------|--------|
| 0 | 0.5-1 天 | 项目骨架 |
| 1 | 1-2 天 | ★命令行能聊天 |
| 2 | 2-3 天 | ★agent 会用工具 |
| 3 | 1-2 天 | 有记忆 |
| 4 | 1-2 天 | 配置化 |
| 5 | 每项 2-5 天 | 按兴趣扩展 |

## 风险与注意事项

- **API key 安全:** key 只放 `.env`（gitignore 排除），绝不提交
- **shell 工具安全:** 阶段 2 的 shell 工具是双刃剑，先做成"执行前打印确认"或仅限只读命令，后续再放开
- **上下文长度:** 阶段 3 后历史会膨胀，阶段 4 的 history_limit 裁剪是必须项
- **中文路径:** Windows 下工具处理路径时注意编码（此前 git-bash 已踩过 MSYS 路径转换的坑）
- **别过度设计:** 每个阶段只做该阶段的事，YAGNI

## 开放问题

1. 阶段 5 想先做哪个方向？（技能 / 流式 / 定时任务 / 平台 / MCP）
2. 是否需要先接一个 Web UI（参照 Hermes desktop）还是保持纯 CLI 一段时间？
3. 练手是否希望每阶段都走 TDD（推荐）还是先求跑通再补测试？

---

*计划生成：2026-08-14 · 基于本地 Hermes 源码架构参考（hermes-agent 仓库）*
