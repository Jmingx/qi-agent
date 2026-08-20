"""Agent 工厂：统一构建"真实形态"的 agent（cli 与 eval 共用）。

设计（方案 docs/plans/2026-08-20-测评系统阶段A方案.md）：
- eval/prod parity：评测必须测真实运行形态（含插件装配），否则测的是
  "不存在的 agent"（裸 Agent 无 env_info/security_guard）
- cli.py 的装配逻辑收敛于此——单一真实路径，将来加插件 eval 自动跟随
"""

import os

from dotenv import load_dotenv

from qi_agent.agent import Agent
from qi_agent.debugger import DebugLogger
from qi_agent.llm import LLMClient
from qi_agent.plugins import load_plugins
from qi_agent.plugins.config import load_plugin_config


def load_api_key() -> str:
    """从 .env 加载 DeepSeek API key，缺失时给出明确报错。"""
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "未找到 DEEPSEEK_API_KEY。\n"
            "请复制 .env.example 为 .env 并填入你的 DeepSeek API key。"
        )
    return api_key


def build_agent(debug: bool = False, stats: bool = False) -> tuple[Agent, list]:
    """构建 agent（真实形态）：LLM 客户端 + 插件装配（plugins.toml 配置驱动）。

    Args:
        debug: 注入 DebugLogger（CLI --debug）
        stats: 快捷启用 tool_stats（CLI --stats，向配置注入，不改配置文件）

    Returns:
        (agent, installed_plugins)——installed 供 CLI 会话结束打印 report()
    """
    api_key = load_api_key()
    logger = DebugLogger() if debug else None
    agent = Agent(LLMClient(api_key), logger=logger)

    # 插件装配（v0.4.9）：注册表 + 配置文件，加插件不再改这里
    plugin_config = load_plugin_config()
    if stats:
        plugin_config["tool_stats"] = {"enabled": True}
    installed = load_plugins(agent.events, plugin_config)
    return agent, installed
