"""Subagent 管理器：任务运行环境（Context）+ 半双工协议（方案 2026-08-23）。

半双工协议（第 2.3 节）——父单向控 + 子单向回报：
  父 → 子：steer（注入补充指令，子下轮生效）/ poll（查状态）/ stop（强制终止）
  子 → 父：result（最终结果，结构化）/ partial（need_more_info 回报）
  协商落地 = partial 回报 + 重新 spawn（不是实时对话，绕开同步工具调用死锁）

生命周期状态机（第 2.4 节）：
  spawn → running → completed（result）
                  → failed（error，含 timeout）
                  → stopped（父 stop 强制终止）

为什么不全双工：父 agent 正阻塞在 delegate_task 工具调用里，子问父答
  → 死锁。业界主流（Hermes steer / DSH direction）都是单向。

AgentContext 统一合并（方案 2026-08-24）：
  SubagentContext = AgentContext（统一数据载体）+ 子专属配置
  （write_paths 授权清单 / timeout）。控制面（steer/stop/poll/wait）
  由统一 AgentContext 提供——任何控制者都能用（父 agent / 用户 / CLI）。

AgentManager 统一控制台（方案 2026-08-24 更新）：
  SubagentManager 构建升级为 AgentManager（agent_manager.py）——本模块
  保留 SubagentContext（子专属配置）+ SubagentManager（向后兼容别名，
  继承 AgentManager）。控制面统一：CLI 控制主 agent = 父 agent 控制
  subagent（同一个 AgentManager）。
"""

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext


class SubagentContext(AgentContext):
    """子 agent 运行环境 = 统一 AgentContext + 子专属配置。

    父 agent（manager.spawn）创建时传 parent=主 agent context；
    write_paths = 授权清单（子 agent 只写这些前缀内的路径）；
    timeout = 子任务超时（超时 → FAILED）。
    """

    def __init__(
        self,
        session_id: str,
        goal: str,
        context: str = "",
        timeout: float = 120.0,
        max_turns: int = 8,
        events=None,
        parent: AgentContext | None = None,
        write_paths: list[str] | None = None,
    ) -> None:
        super().__init__(
            context_id=session_id, goal=goal, parent=parent,
            persist=False,  # 子 agent 默认瞬态（审计可显式开）
            max_turns=max_turns, events=events,
        )
        self.context_text = context  # 背景信息（父提炼，注入子 system prompt）
        self.timeout = timeout
        self.write_paths = write_paths or []
        # spawn 语义 = 立即运行（不是"新建未开始"）→ 初始化即 RUNNING
        # （主 agent 的 IDLE 是"未开始"，子 agent 创建即开始，语义不同）
        self.begin_chat()


class SubagentManager(AgentManager):
    """子任务管理器（向后兼容别名——AgentManager 的子类）。

    方案 2026-08-24：SubagentManager 构建升级为 AgentManager（统一控制台，
    主/子 agent 共用控制面）。本类保留名字与接口（spawn/steer/stop/poll），
    现有测试/调用方零改动；新增能力（register 主 agent）走 AgentManager。
    """

    def __init__(self, max_concurrent: int = 3) -> None:
        super().__init__(max_concurrent=max_concurrent)
    # spawn/_run/steer/stop/poll 全部继承自 AgentManager（接口不变）

