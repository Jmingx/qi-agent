"""Agent 工厂：统一构建"真实形态"的 agent（cli 与 eval 共用）。

设计（方案 docs/plans/2026-08-20-测评系统阶段A方案.md）：
- eval/prod parity：评测必须测真实运行形态（含插件装配），否则测的是
  "不存在的 agent"（裸 Agent 无 env_info/security_guard）
- cli.py 的装配逻辑收敛于此——单一真实路径，将来加插件 eval 自动跟随
"""

import os

from dotenv import load_dotenv

from qi_agent.agent import Agent
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


def build_agent(debug: bool = False, stats: bool = False,
                interactive: bool = True) -> tuple[Agent, list]:
    """构建 agent（真实形态）：LLM 客户端 + 插件装配（plugins.toml 配置驱动）。

    Args:
        debug: 装配 debug_logger 插件（CLI --debug，事件驱动日志）
        stats: 快捷启用 tool_stats（CLI --stats，向配置注入，不改配置文件）
        interactive: 是否交互环境（CLI=True）——False（评测/自动化）时不装配
            approval_gate 审批插件 → 需审批命令 fail-closed 拒绝。
            不能靠 isatty 判断（CLI 终端跑评测 stdin 也是 tty，会真弹窗卡住）

    Returns:
        (agent, installed_plugins)——installed 供 CLI 会话结束打印 report()
    """
    api_key = load_api_key()
    agent = Agent(LLMClient(api_key))

    # 插件装配（v0.4.9）：注册表 + 配置文件，加插件不再改这里
    plugin_config = load_plugin_config()
    if debug:
        # --debug：装配调试日志插件（事件驱动打印 [USER]/[REQ]/[RESP] 等，
        # 2026-08-22 插件化——不再注入 Agent.logger）
        plugin_config["debug_logger"] = {"enabled": True}
    if stats:
        plugin_config["tool_stats"] = {"enabled": True}
    if not interactive:
        # 无交互环境（评测/自动化）：审批插件 + 资源监控不装配 → fail-closed
        # 拒绝 + 评测输出零污染（资源监控默认打印状态行会污染评测输出）
        plugin_config["approval_gate"] = {"enabled": False}
        plugin_config["resource_monitor"] = {"enabled": False}
    installed = load_plugins(agent.events, plugin_config)
    return agent, installed
