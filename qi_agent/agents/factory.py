"""Agent 工厂：统一构建"真实形态"的 agent（cli 与 eval 共用）。

设计（方案 docs/plans/2026-08-20-测评系统阶段A方案.md）：
- eval/prod parity：评测必须测真实运行形态（含插件装配），否则测的是
  "不存在的 agent"（裸 Agent 无 env_info/security_guard）
- cli.py 的装配逻辑收敛于此——单一真实路径，将来加插件 eval 自动跟随
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from qi_agent.agents.agent import Agent
from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext
from qi_agent.events import EventBus
from qi_agent.llm import LLMClient
from qi_agent.plugins import load_plugins
from qi_agent.plugins.config import load_plugin_config


@dataclass
class AgentBundle:
    """build_agent 返回形态（方案 2026-08-24 D4）——命名访问，避免元组位置错。

    agent: 主 agent（执行者——只做行为：chat）
    manager: 统一控制台（CLI/父 agent 控制 + 数据访问的唯一入口）
    context_id: 主 agent 数据载体在控制台的 id（CLI 用
        manager.get_context(context_id) 访问数据——不直接持有 context 对象）
    agent_id: 主 agent 在控制台的 id（/stop /status 寻址）
    installed: 已装配插件列表（会话结束打印 report）
    """

    agent: Agent
    manager: AgentManager
    context_id: str
    agent_id: str
    installed: list = field(default_factory=list)

# 生产装配的系统提示词（subagent 方案 2026-08-23）：
# 默认 Agent 提示词保持简单（"你是一个有用的助手"），但真实装配（CLI/评测）
# 使用本增强版——模型需要知道 subagent 能力的存在与使用时机，
# 否则 delegate_task 工具注册了也不会被调用（评测 d1/d2 首跑实测：
# 主 agent 自己用 list_dir/read_file 干活，没用 delegate_task）。
PROD_SYSTEM_PROMPT = (
    "你是一个有用的助手，拥有多种工具能力。\n\n"
    "工具使用策略：\n"
    "- 简单任务（问时间/读单个文件/快速计算）：直接用对应工具。\n"
    "- 独立可外包任务（批量调研/长文档分析/多文件整理）：使用 "
    "delegate_task 工具委派给 subagent 独立完成——subagent 有独立的"
    "上下文，适合处理会污染主对话的大任务。它会返回结构化 JSON。\n"
    "- 不要重复 subagent 已完成的工作，直接消费它的 summary 结果。\n"
    "- 如果工具调用被拒绝（审批/拦截），换一种安全方式或告知用户。"
)


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
                interactive: bool = True,
                plugin_overrides: dict | None = None) -> tuple[Agent, list]:
    """构建 agent（真实形态）：LLM 客户端 + 插件装配（plugins.toml 配置驱动）。

    Args:
        debug: 装配 debug_logger 插件（CLI --debug，事件驱动日志）
        stats: 快捷启用 tool_stats（CLI --stats，向配置注入，不改配置文件）
        interactive: 是否交互环境（CLI=True）——False（评测/自动化）时不装配
            approval_gate 审批插件 → 需审批命令 fail-closed 拒绝。
            不能靠 isatty 判断（CLI 终端跑评测 stdin 也是 tty，会真弹窗卡住）
        plugin_overrides: 任务级插件配置覆盖（评测专用，CLI 不传）——
            深合并进插件配置（如 L3 长对话评测覆盖 context_manager 小窗口
            触发压缩，方案 2026-08-23）

    Returns:
        (agent, installed_plugins)——installed 供 CLI 会话结束打印 report()
    """
    api_key = load_api_key()
    # AgentManager 统一控制台（方案 2026-08-24）：context 由 factory 创建
    # （恢复点——"无状态 Agent 可被新实例接管"的落点），主 agent 注册进
    # manager（CLI 通过 manager 控制，eval/prod parity 同一装配）
    events = EventBus()
    context = AgentContext(persist=True, events=events)
    agent = Agent(LLMClient(api_key), system_prompt=PROD_SYSTEM_PROMPT,
                  context=context)
    manager = AgentManager()
    agent_id = manager.register(context, role="main")

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
    if plugin_overrides:
        # 任务级覆盖（评测专用）：深合并——overrides 的值逐层覆盖配置文件
        # （{**base, **override} 只合并顶层，嵌套 dict 需递归）
        plugin_config = _deep_merge(plugin_config, plugin_overrides)
    installed = load_plugins(agent.events, plugin_config)
    return AgentBundle(agent=agent, manager=manager,
                       context_id=context.id, agent_id=agent_id,
                       installed=installed)


def _deep_merge(base: dict, override: dict) -> dict:
    """深合并两个配置 dict（override 的值覆盖 base，嵌套 dict 递归合并）。

    顶层插件名 → 插件配置 dict；插件配置内部可能嵌套（如
    context_manager.compress = {window, threshold}）——必须递归，
    否则 override 的 compress 会整体替换掉 base 的其他字段。
    """
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
