"""agents 包：执行者家族（可插拔 agent，方案 2026-08-24 归类）。

设计（对齐 tools/plugins 的"机制层+本体层"分层哲学）：
  agents/ = "有哪些执行者可以用"（可插拔边界——换执行者=本包加文件）
    agent.py         Agent 执行者（无状态循环）
    agent_manager.py AgentManager 统一控制台（register/spawn/steer/stop/poll）
    factory.py       build_agent + AgentBundle + PROD_SYSTEM_PROMPT（装配）

注（方案 2026-08-29-Subagent类型收敛）：SubagentContext/SubagentManager
已删除——所有 agent（主/子）统一 AgentManager + AgentContext（子专属
字段 write_paths/timeout 归拢在 AgentContext 并加注释）。

与正交基础设施的边界（不在本包）：
  context/  = 数据载体（AgentContext——执行者跑在什么数据上，session 接入点）
  events.py = 事件总线（全项目共用）
  llm.py    = LLM 客户端（全项目共用）

换执行者 = agents/ 加文件（如 agents/specialist.py），
context/events/llm 零改动——插拔边界清晰。
"""

from qi_agent.agents.agent import Agent
from qi_agent.agents.agent_manager import AgentManager
from qi_agent.agents.factory import RuntimeBundle, build_runtime, make_agent
from qi_agent.agents.pool import AgentPool

__all__ = [
    "Agent",
    "AgentManager",
    "AgentPool",
    "RuntimeBundle",
    "build_runtime",
    "make_agent",
]
