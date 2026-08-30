"""delegate_task 工具：subagent（agent-as-tool，方案 2026-08-23）。

核心设计：
- 工具形态：delegate_task 注册为普通工具，主 agent 自己决定外包
- 嵌套 Agent：子 agent = 独立 Agent 实例（有工具能力 + 事件循环 + 安全链）
- 受限子集（Phase 1 双层）：子 agent 只见白名单工具（schema 过滤 + 执行校验）
- 授权清单：write_paths 白名单匹配——子 agent 不弹窗，查询清单命中即走
- 结构化返回（P0）：result 是 JSON（summary/artifacts/status/error/question/usage）
- 递归禁止：子 agent 默认子集没有 delegate_task → 结构上无法再 spawn

安全底线（硬编码）：
- write_paths 为空 → write_file 一律拒绝
- 危险工具（shell 代码执行等）永远不在默认子集
"""

import json
from typing import Callable

from qi_agent.events import EventBus
from qi_agent.llm import LLMClient
from qi_agent.tools.registry import register

# 注意：不能模块级 import qi_agent.agents.agent.Agent——tools/builtin/__init__ 在
# qi_agent.tools 包初始化时导入本模块，而 agent.py 又 import tools/__init__
# （registry），循环导入。Agent 在 _run_subagent 内延迟 import（见下）。

# 默认只读子集（层 0）：无需审批的只读工具 + 只读 run_python
# 方案 4.3 权限分层——层 0 默认子集，不需要任何授权
DEFAULT_READONLY_TOOLS = [
    "read_file", "search_files", "get_time", "list_dir",
    "web_search", "web_extract",
]

# 危险工具黑名单（层 3，硬编码）：永远不在子 agent 子集
# （shell 代码执行、rm 等破坏性工具——即使主 agent 请求也排除）
_FORBIDDEN_TOOLS = {"shell", "run_python", "delegate_task"}

# 结构化返回 schema 提示（P0 用户要求）：子 agent 必须按此 JSON 产出最终回答
_SUBAGENT_PROMPT = (
    "你是一个子任务代理（subagent）。你的任务目标：{goal}\n\n"
    "背景信息：{context}\n\n"
    "你必须独立完成这个任务，然后【只输出一个 JSON 对象】作为最终回答"
    "（不要输出任何其他文字），格式：\n"
    '{{"summary": "任务完成总结（父代理可直接使用的结论）", '
    '"artifacts": ["产出文件路径列表，无则为空数组"], '
    '"status": "completed 或 failed 或 need_more_info", '
    '"error": "失败原因（status 为 failed 时必填，否则 null）", '
    '"question": "need_more_info 时向父代理询问的问题，否则 null"}}\n\n'
    "注意：如果信息不足，status 用 need_more_info 并在 question 说明需要什么。"
)


def delegate_task(
    goal: str,
    context: str = "",
    tools: list[str] | None = None,
    write_paths: list[str] | None = None,
    max_turns: int = 8,
    _client_factory: Callable | None = None,
    _tool_executor_factory: Callable | None = None,
) -> str:
    """执行一个子任务（subagent，agent-as-tool）。

    Args:
        goal: 任务目标（必传，丢给子 agent）
        context: 父提炼的背景（主 agent 在发起调用时写清楚）
        tools: 子 agent 工具白名单（None = 默认只读子集）——
            主 agent 按任务性质给权限；危险工具永远被排除（硬编码）
        write_paths: 可写路径白名单（授权清单）——子 agent 只能写
            这些前缀内的路径；空列表 = 无写权限（write_file 一律拒绝）
        max_turns: 子 agent 最大对话轮数（预算兜底，防失控）
        _client_factory: 测试注入（生产用 load_api_key + LLMClient）
        _tool_executor_factory: 测试注入（生产用 ToolExecutor）

    Returns:
        结构化 JSON 字符串（summary/artifacts/status/error/question/usage）
    """
    # 组装受限子集 + 装配子 agent（与 manager 后台模式共用 _run_subagent）
    session = _ContextAdapter(goal, context, max_turns, write_paths)
    try:
        result = _run_subagent(
            session, _client_factory, _tool_executor_factory, tools, write_paths,
        )
    except Exception as exc:
        return json.dumps({
            "summary": "", "artifacts": [], "status": "failed",
            "error": f"子任务执行异常: {exc}", "question": None,
        }, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


class _ContextAdapter:
    """把 delegate_task 同步调用适配成 SubagentContext 接口（steer/stop 空实现）。

    manager 后台模式传真 SubagentContext（有 steer/stop）；同步工具调用
    传本适配器（无控制面，行为等价）。字段对齐 SubagentContext
    （goal / context_text / max_turns / write_paths）。
    """

    def __init__(self, goal: str, context: str, max_turns: int,
                 write_paths: list[str] | None) -> None:
        self.goal = goal
        self.context_text = context
        self.max_turns = max_turns
        self.write_paths = write_paths or []
        self.status = "running"

    def drain_steer(self) -> list[str]:
        return []

    def should_stop(self) -> bool:
        return False


def _run_subagent(
    session,
    client_factory: Callable | None,
    tool_executor_factory: Callable | None,
    tools: list[str] | None,
    write_paths: list[str] | None,
) -> dict:
    """装配并运行子 agent（delegate_task 同步模式与 manager 后台模式共用）。

    Args:
        session: SubagentContext（manager 模式）或 _ContextAdapter（同步模式）——
            提供 goal/context/max_turns/write_paths + drain_steer/should_stop
        client_factory: LLM 客户端工厂（测试注入；None → 生产默认）
        tool_executor_factory: 执行器工厂（测试注入）
        tools: 工具白名单（None = 默认只读子集）
        write_paths: 可写路径白名单（授权清单）

    Returns:
        结构化结果 dict（summary/artifacts/status/error/question/usage）
    """
    # 1. 组装受限子集：用户指定 ∪ 默认只读，排除危险工具（层 3）
    allowlist = set(DEFAULT_READONLY_TOOLS)
    if tools:
        allowlist.update(tools)
    allowlist -= _FORBIDDEN_TOOLS
    if write_paths:
        allowlist.add("write_file")
    allowlist = sorted(allowlist)

    # 2. 子 agent 装配：独立 client + 独立事件总线 + 授权清单
    # （延迟 import Agent——避免模块级循环导入，见文件头注释）
    from qi_agent.agents.agent import Agent

    client = client_factory() if client_factory else _build_client()
    # 2026-08-30：总线绑定子 context_id（日志定位——on/emit context=agt_xxx）
    # getattr 兜底：同步模式 _ContextAdapter 无 id（空——日志无 context）
    events = EventBus(context_id=getattr(session, "id", ""))
    events.on("agent/tool-approval", _make_approval_handler(write_paths or []))
    executor = (
        tool_executor_factory(events) if tool_executor_factory
        else _build_executor(events)
    )
    # system_prompt：manager 模式已在 ctx 算好（方案 2026-08-29 收敛——
    # context_text 消除，spawn 时直接算）；同步模式 _ContextAdapter 没有
    # → 回退现算（字段对齐兜底）
    system_prompt = getattr(session, "system_prompt", "") or (
        _SUBAGENT_PROMPT.format(goal=session.goal,
                                context=getattr(session, "context_text", "")))
    subagent = Agent(
        client,
        system_prompt=system_prompt,
        max_turns=session.max_turns,
        events=events,
        tool_executor=executor,
        tools=allowlist,
    )

    # 3. 跑子任务（steer/stop 注入：子 agent 每轮检查——半双工控制面）

    stop_hook = getattr(session, "should_stop", lambda: False)

    def _stop_watcher(messages, **extra):
        """pre-step 瀑布钩子：检查 stop（实时中断——消息注入已收敛到
        Agent._consume_mailbox——2026-08-30 主/子统一每轮消费）。"""
        if stop_hook():
            raise _StopRequested()
        return messages

    events.on("agent/pre-step", _stop_watcher, priority=200)

    try:
        subagent.chat(session.goal)
    except _StopRequested:
        return {
            "summary": "", "artifacts": [], "status": "stopped",
            "error": "父代理强制终止", "question": None,
        }
    except Exception as exc:
        return {
            "summary": "", "artifacts": [], "status": "failed",
            "error": f"子任务执行异常: {exc}", "question": None,
        }

    # 4. 解析最终回答为结构化 JSON（子 agent 被要求只输出 JSON）
    final = _extract_final_answer(subagent)
    try:
        data = json.loads(final)
        if not isinstance(data, dict):
            raise ValueError("不是 JSON 对象")
    except (json.JSONDecodeError, ValueError):
        # 兜底：解析失败也按结构化返回（summary=原文）
        data = {
            "summary": final, "artifacts": [], "status": "completed",
            "error": None, "question": None,
        }
    data.setdefault("artifacts", [])
    data.setdefault("usage", subagent.get_usage())
    return data


class _StopRequested(Exception):
    """内部信号：子 agent 被父 stop（pre-step 钩子抛出）。"""


def _build_client() -> LLMClient:
    """生产用 client：从 .env 加载 API key。"""
    from qi_agent.agents.factory import load_api_key

    return LLMClient(load_api_key())


def _build_executor(events: EventBus):
    """生产用 executor：默认 ToolExecutor（挂在子事件总线上）。"""
    from qi_agent.tools.executor import ToolExecutor

    return ToolExecutor(events)


def _extract_final_answer(subagent) -> str:
    """提取子 agent 最终回答（最后一条 assistant 消息内容）。"""
    for msg in reversed(subagent.messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return str(msg["content"])
    return ""


def _make_approval_handler(write_paths: list[str]):
    """构造子 agent 的审批处理器（授权清单匹配，不弹窗）。

    子 agent 没有交互层——审批不能弹窗，只能查授权清单：
    - write_file(path) → path 在 write_paths 前缀内 → 放行；否则拒绝
    - 其他工具 → 放行（受限子集已保证工具本身安全）
    - fail-closed：任何异常 → 拒绝
    """

    def handler(name: str, arguments: dict, **_) -> bool:
        return _approve_tool(name, arguments, write_paths)

    return handler


def _approve_tool(name: str, arguments: dict, write_paths: list[str]) -> bool:
    """授权判定（独立函数，便于测试）：返回 True 放行 / False 拒绝。"""
    if name != "write_file":
        return True  # 只读子集内的工具直接放行
    path = str(arguments.get("path", ""))
    if not write_paths:
        return False  # 无写权限 → 一律拒绝（安全底线）
    # 路径前缀匹配（白名单）
    return any(path.startswith(p) for p in write_paths)


register(
    name="delegate_task",
    toolset="builtin",
    handler=delegate_task,
    description=(
        "委派子任务给 subagent（独立 agent 执行）：传入 goal（任务目标）"
        "和 context（背景信息）。subagent 在受限工具集内独立完成，返回结构化 JSON"
        "（summary 总结 / artifacts 产出 / status 状态 / error 错误 / question 询问）。"
        "适合：独立调研、并行分析、长文档处理等可外包的子任务。"
    ),
    # 条件审批（规则化，v0.4.27）：approval="subagent" 查 rules 的
    # TOOL_APPROVAL_RULES 表（条件逻辑单一数据源）：
    # 纯只读委派（无 write_paths）→ 安全放行（子 agent 无写权限+无危险工具）
    # 带写权限的委派（write_paths 非空）→ 用户背书（白名单外权限弹框审批）
    approval="subagent",
)
