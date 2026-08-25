"""AgentContext：统一运行环境（所有 agent 共用，方案 2026-08-24）。

背景（AgentContext 统一合并方案）：
  主 agent（Agent 类内部）与 SubagentContext 曾有 80% 重叠——都是
  "一个 agent 运行的【环境】"（状态 + 事件 + 控制面）。差异只在
  控制者（用户/CLI vs 父 agent）与持久化（长期 vs 瞬态）。

职责边界（关键架构决策，用户拍板 D2/D3）：
  AgentContext = 【数据载体】——消息历史 + 会话轮数 + 用量累计
    + 状态机 + 控制面 + 事件总线。可持久化、可恢复、可归档。
  Agent = 【无状态执行者】——消费/回填 Context 的消息，只跑循环。
  为什么这样分：
  - session/记忆系统的接入点是【数据载体】不是【执行者】
    （session 只碰 Context，不依赖 Agent 循环）
  - Agent 实例销毁后 Context 还在 → 消息可归档可持久化
  - 无状态 Agent 可被新实例接管继续跑（断线续聊/会话恢复基础）
  - 生命周期判据：消息/轮数/用量是【会话级】（跨 chat/跨实例）→ 归 Context；
    step（循环步数）是【循环级】→ 留循环局部变量

控制面通用化（半双工协议，源自 subagent 方案）：
  steer/stop/poll 对【任何 agent】可用——子 agent 由父（manager）控制，
  主 agent 未来由用户/CLI 控制（CLI /stop = context.stop()，同一套机制）。
"""

import threading
import uuid

from enum import Enum

from qi_agent.events import EventBus


class ContextStatus(str, Enum):
    """agent 运行状态（主/子统一）。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class AgentContext:
    """统一运行环境：数据载体（消息/轮数/用量）+ 状态机 + 控制面。"""

    def __init__(
        self,
        agent_id: str | None = None,
        goal: str = "",
        parent: "AgentContext | None" = None,
        persist: bool = False,
        max_turns: int = 8,
        events: EventBus | None = None,
    ) -> None:
        self.id = agent_id or uuid.uuid4().hex[:12]
        self.goal = goal
        self.parent = parent
        self.persist = persist
        self.max_turns = max_turns
        self.events = events or EventBus()

        # 数据载体（session/记忆系统的接入点）
        self.messages: list[dict] = []
        self.turn: int = 0  # 会话轮数（用户消息条数，跨 chat 累计）
        self.usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

        # 状态机
        self.status = ContextStatus.RUNNING
        self.result: dict | None = None
        self.error: str | None = None

        # 控制面（任何控制者都能用：父 agent / 用户 / CLI）
        self.steer_queue: list[str] = []  # 注入的补充指令（下轮消费）
        self._stop_flag = threading.Event()
        self._done = threading.Event()

    # ── 控制面（控制者侧调用）────────────────────────────────────────────
    def steer(self, message: str) -> None:
        """注入补充指令（agent 下轮生效）。"""
        self.steer_queue.append(message)
        self.events.emit("subagent/steer", session_id=self.id, message=message)

    def stop(self) -> None:
        """强制终止（agent 下轮检查标志退出）。"""
        self._stop_flag.set()
        self.events.emit("subagent/stop", session_id=self.id)
        if self.status == ContextStatus.RUNNING:
            self.status = ContextStatus.STOPPED
            self.error = "父代理强制终止"
        self._done.set()  # 释放等待者（wait 立即返回）

    def poll(self) -> ContextStatus:
        """查询状态（探活）。"""
        return self.status

    def wait(self, timeout: float | None = None) -> dict | None:
        """阻塞等待任务结束，返回结果（超时返回 None）。"""
        self._done.wait(timeout=timeout)
        if self._done.is_set():
            return self.result
        # 等待超时：如果还在跑，标记失败（超时兜底）
        if self.status == ContextStatus.RUNNING:
            self.fail(f"任务超时（>{timeout}s）")
        return self.result

    # ── agent 侧调用（循环内每轮检查）────────────────────────────────────
    def drain_steer(self) -> list[str]:
        """取走待处理的补充指令（每轮检查——下轮生效）。"""
        msgs = list(self.steer_queue)
        self.steer_queue.clear()
        return msgs

    def should_stop(self) -> bool:
        """是否被要求终止（每轮检查）。"""
        return self._stop_flag.is_set()

    def complete(self, result: dict) -> None:
        """正常完成（状态 COMPLETED，结果带回）。"""
        self.result = result
        self.status = ContextStatus.COMPLETED
        self._done.set()

    def fail(self, error: str) -> None:
        """失败（异常/超时，状态 FAILED）。"""
        self.error = error
        self.status = ContextStatus.FAILED
        self._done.set()

    def reset(self) -> None:
        """清空会话（clear_context 语义：消息/轮数重置，控制面复位）。"""
        self.messages.clear()
        self.turn = 0
        self.status = ContextStatus.RUNNING
        self.result = None
        self.error = None
        self.steer_queue.clear()
        self._stop_flag.clear()
        self._done.clear()
