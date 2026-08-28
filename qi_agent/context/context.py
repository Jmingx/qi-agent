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

from datetime import datetime
from enum import Enum

from qi_agent.events import EventBus


def generate_id(prefix: str) -> str:
    """生成可读 ID：<前缀>_<YYYYMMDD_HHMMSS>_<6位随机>。

    时间戳后缀（用户拍板 2026-08-27）：一眼看出创建时间/事件顺序；
    随机位防同秒冲突。用于 ctx_（数据载体）/ agt_（执行者）。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = uuid.uuid4().hex[:6]
    return f"{prefix}_{ts}_{rand}"


class ContextStatus(str, Enum):
    """agent 运行状态（主/子统一，会话级——整个生命周期）。

    状态转移（方案 2026-08-24-AgentManager统一控制台 §4.5）：
      IDLE → RUNNING → COMPLETED / FAILED / STOPPED
      （reset 后任意终态回到 IDLE，可复用）
    """

    IDLE = "idle"              # 新建未开始（2026-08-24 新增）
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class ChatPhase(str, Enum):
    """chat 内部阶段（循环级——单次 chat 调用进行到哪）。

    状态转移（方案 2026-08-24-AgentManager统一控制台 §4.5）：
      IDLE → TURN_START → LLM_CALL →（TOOL_EXEC → LLM_CALL 循环）→ ANSWERING → DONE
      任何阶段 stop → DONE（下轮生效本阶段；后台线程/信号下阶段实时中断）
    """

    IDLE = "idle"
    TURN_START = "turn_start"   # 用户输入已接收
    LLM_CALL = "llm_call"       # LLM 调用中
    TOOL_EXEC = "tool_exec"     # 工具执行中
    ANSWERING = "answering"     # 最终回答
    DONE = "done"               # 本次 chat 结束


class AgentContext:
    """统一运行环境：数据载体（消息/轮数/用量）+ 状态机 + 控制面。"""

    def __init__(
        self,
        goal: str = "",
        parent: "AgentContext | None" = None,
        persist: bool = False,
        max_turns: int = 8,
        events: EventBus | None = None,
        context_id: str | None = None,
    ) -> None:
        self.id = context_id or generate_id("ctx")
        # id 前缀（方案 2026-08-24-执行权归还Manager与ID规范化）：
        # ctx_ = 数据载体（会话身份，持久化键——恢复会话时传原 id）；
        # agent 用 agt_（执行者身份，在 Agent 上，不在 context）
        # 格式：<前缀>_<YYYYMMDD_HHMMSS>_<6位随机>（时间戳可读 + 随机防冲突）
        self.goal = goal
        self.parent = parent
        self.persist = persist
        self.max_turns = max_turns
        self.events = events or EventBus()
        # system_prompt（数据载体的一部分——system 消息初始化属于数据初始化，
        # 2026-08-24 用户拍板：clear/reset 挪到 context，重建 system 需要它）
        self.system_prompt = ""

        # 数据载体（session/记忆系统的接入点）
        self.messages: list[dict] = []
        self.turn: int = 0  # 会话轮数（用户消息条数，跨 chat 累计）
        self.usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

        # 主动记忆（方案 2026-08-26-主动记忆系统）：每 N 轮触发提炼
        self.memory_extract_interval = 10  # 提炼间隔（用户拍板：至少每 10 轮）
        self.last_extract_turn = 0  # 上次提炼的轮数（防重复触发）

        # 状态机（两级，方案 2026-08-24 §4.5）
        self.status = ContextStatus.IDLE  # 会话级：新建未开始
        self.phase = ChatPhase.IDLE       # 循环级：当前 chat 内部阶段
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
        self.phase = ChatPhase.DONE
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

    def wait_stop_or_done(self, timeout: float | None = None) -> str:
        """等待"被停止"或"chat 完成"，返回结果类型（stopped/done/timeout）。

        方案 2026-08-24-stop实时中断（Phase A）：manager.run 主线程在这里等
        后台 LLM 线程——stop 触发立即返回"stopped"（实时中断），
        LLM 先完成返回"done"，超时返回"timeout"。

        注意：stop() 内部会 set _done（释放等待者）——所以 stop 后
        _done 也 set，这里必须【先查 _stop_flag】（stop 优先）。
        """
        self._done.wait(timeout=timeout)  # 等 chat 完成（stop 也会 set）
        if self._stop_flag.is_set():
            return "stopped"   # stop 优先（即使 done 也 set）
        if self._done.is_set():
            return "done"
        return "timeout"

    def complete(self, result: dict) -> None:
        """正常完成（状态 COMPLETED，结果带回）。"""
        self.result = result
        self.status = ContextStatus.COMPLETED
        self.phase = ChatPhase.DONE
        self._done.set()

    def fail(self, error: str) -> None:
        """失败（异常/超时，状态 FAILED）。"""
        self.error = error
        self.status = ContextStatus.FAILED
        self.phase = ChatPhase.DONE
        self._done.set()

    # ── chat 生命周期状态转移（方案 2026-08-24 §4.5，主 agent 用）────────
    def begin_chat(self) -> None:
        """chat() 入口：IDLE → RUNNING + TURN_START。"""
        self.status = ContextStatus.RUNNING
        self.phase = ChatPhase.TURN_START

    def enter_llm_call(self) -> None:
        """循环每步调 LLM：→ LLM_CALL。"""
        self.phase = ChatPhase.LLM_CALL

    def enter_tool_exec(self) -> None:
        """模型要调工具：→ TOOL_EXEC。"""
        self.phase = ChatPhase.TOOL_EXEC

    def enter_answering(self) -> None:
        """模型直接回答：→ ANSWERING。"""
        self.phase = ChatPhase.ANSWERING

    def complete_chat(self, result: dict | None = None) -> None:
        """chat 正常结束：RUNNING → COMPLETED + DONE。"""
        if result is not None:
            self.result = result
        self.status = ContextStatus.COMPLETED
        self.phase = ChatPhase.DONE
        self._done.set()

    def fail_chat(self, error: str) -> None:
        """chat 异常：RUNNING → FAILED + DONE。"""
        self.error = error
        self.status = ContextStatus.FAILED
        self.phase = ChatPhase.DONE
        self._done.set()

    def reset(self) -> None:
        """清空会话（clear_context 语义：消息/轮数重置，控制面复位）。

        状态机：任意终态 → IDLE + phase IDLE（可复用）。
        """
        self.messages.clear()
        self.turn = 0
        self.status = ContextStatus.IDLE
        self.phase = ChatPhase.IDLE
        self.result = None
        self.error = None
        self.steer_queue.clear()
        self._stop_flag.clear()
        self._done.clear()

    def reset_session(self) -> None:
        """重置会话并重建 system 消息（原 agent.clear_context 语义）。

        2026-08-24 用户拍板：clear 是"数据载体重置"不是"执行者行为"，
        挪到 context。system_prompt 由 Agent 装配时写入（数据初始化）。
        """
        self.reset()
        self.messages = [
            {"role": "system", "content": self.system_prompt},
        ]
        self.turn = 0
