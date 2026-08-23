# qi-agent

qi-agent 是一个轻量级、插件化的 Python Agent 框架。基于事件驱动架构，
提供完整的工具系统、安全审批、上下文管理与任务级评测能力。

## 特性

| 能力 | 说明 |
|------|------|
| **工具系统** | 注册制架构（1 文件 1 工具），schema 自动生成、参数校验、多工具并行执行 |
| **内置工具** | 文件域（read_file / write_file / patch / list_dir / search_files / file_delete）、shell、沙箱 Python 执行、web_search / web_extract（双后端）、todo 任务清单、clarify 澄清提问、get_time |
| **安全体系** | 沙箱执行（受限 Python + 资源限制）、三档审批机制（自动放行 / 需审批 / 红线硬拒）、敏感路径保护、命令权限规则单一来源 |
| **插件架构** | 横切关注点全部插件化（事件驱动）：安全判档、审批交互、上下文管理、调试日志、资源监控——agent 核心零侵入 |
| **上下文管理** | token 估算 / 构成分解 / 滑动窗口裁剪 / sticky 关键信息保留（消息组成对、role 交替、锚点注入等协议安全保证） |
| **任务级评测** | 任务成功率评测、回归基线对比、安全对抗测试——每次改动可量化验证 |

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
| `/exit` | 退出 |

## 架构

```
qi_agent/
├── agent.py            # 核心循环（流程骨架）
├── agent_factory.py    # 统一装配（CLI 与评测共用同一真实形态）
├── cli.py              # 命令行 REPL
├── context/            # 上下文管理算法（估算/裁剪/构成分解/注入）
├── interaction.py      # 交互抽象层（终端 / Web UI 可替换）
├── plugins/            # 插件（安全判档/审批/上下文管理/日志/统计）
└── tools/              # 工具（1 文件 1 工具，注册制）
```

三层架构：核心循环（流程骨架）→ 工具（能力单元，注册制）→ 插件（横切
关注点，事件驱动）。新增能力只需注册工具或装配插件，核心零改动。

## 测试

```bash
uv run python -m pytest        # 全量测试
uv run python -m pytest -q     # 快速模式
uv run python -m ruff check qi_agent tests   # 代码风格检查
```
