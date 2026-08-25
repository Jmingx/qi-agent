# AgentContext 统一合并方案（主 agent / subagent 运行环境归一）

日期：2026-08-24
状态：待评审
关联：docs/plans/2026-08-23-subagent方案.md（subagent 方案，已实施）
     principles/16 §9（统一方向讨论沉淀）

## 背景与动机

subagent 实施后（v0.4.27）出现两个"运行环境"概念：

```
SubagentContext（subagent.py）——子任务运行环境：
  任务定义（goal/context/max_turns）+ 状态机（status/result/error）
  + 控制面（steer_queue/_stop_flag/_done）+ 独立事件总线

主 agent（agent.py Agent 类内部）——主 agent 运行状态：
  events（事件总线）+ _turn（轮数）+ messages（消息历史）
  + _usage（用量累计）+ max_turns

本质相同：都是"一个 agent 运行的【环境】"——状态 + 事件 + 控制。
差异只在：控制者（用户/CLI vs 父 agent）+ 持久化（长期 vs 瞬态）。
```

**问题**：两套概念心智分裂（主 agent 没有"Context"，子 agent 没有"messages"），
未来 session 系统（TODO-1）、multi-agent（v2+）都要统一接入点。

## 目标

统一成 `AgentContext`（所有 agent 共用），主 agent 组合持有，
SubagentContext 退化为子 agent 专属配置层。控制面通用化（用户也能
steer/stop 主 agent），session 接入点统一。

## 设计

### 1. AgentContext（统一运行环境）

```python
class AgentContext:
    """统一运行环境：所有 agent（主/子）共用。"""

    def __init__(
        self,
        agent_id: str | None = None,
        goal: str = "",
        parent: "AgentContext | None" = None,   # None = 主 agent；有 = 子 agent
        persist: bool = False,                   # 是否持久化对话（主 True / 子默认 False）
        max_turns: int = 8,
        events: EventBus | None = None,
    ):
        self.id = agent_id or uuid.uuid4().hex[:12]
        self.goal = goal
        self.parent = parent
        self.persist = persist
        self.max_turns = max_turns
        self.events = events or EventBus()

        # 状态机（统一）
        self.status = ContextStatus.RUNNING
        self.result: dict | None = None
        self.error: str | None = None

        # 控制面（统一——任何控制者都能用）
        self.steer_queue: list[str] = []
        self._stop_flag = threading.Event()
        self._done = threading.Event()

    # 控制面（控制者侧调用）
    def steer(self, message: str) -> None: ...
    def stop(self) -> None: ...
    def poll(self) -> ContextStatus: ...
    def wait(self, timeout=None) -> dict | None: ...

    # 子 agent 侧调用（每轮检查）
    def drain_steer(self) -> list[str]: ...
    def should_stop(self) -> bool: ...
    def complete(self, result): ...
    def fail(self, error): ...
```

### 2. 主 agent 接入（组合，消息/轮数/用量迁入 Context）

```python
class Agent:
    def __init__(self, client, ..., context: AgentContext | None = None):
        self.context = context or AgentContext(persist=True)  # 主 agent 默认持久化
        self.events = self.context.events        # 事件总线从 context 取（兼容现有用法）
        self.max_turns = self.context.max_turns  # 同一来源

    # 兼容委托（外部读取方：cli.get_usage / runner._turn / delegate_task）
    @property
    def messages(self): return self.context.messages
    @property
    def _turn(self): return self.context.turn
    def get_usage(self): return self.context.usage
    @property
    def history(self): return self.context.messages
```

关键：**消息历史 + 会话轮数 + 用量累计全部迁入 Context**
（数据载体），Agent 变【无状态执行者】（只跑循环，消费/回填 Context）。

**为什么迁入（D2/D3 架构决策，用户拍板）**：
- 记忆/session 系统的接入点是【数据载体】不是【执行者】——
  session 只碰 Context（消息/轮数/用量都在里面），不依赖 Agent 循环
- subagent 的 Agent 实例跑完销毁，但 Context 还在 → 消息可归档可持久化
- Agent 无状态 → 同一 Context 可被新 Agent 实例接管继续跑
  （断线续聊/会话恢复的架构基础！）
- 生命周期判据：消息/轮数/用量都是【会话生命周期】（跨 chat、跨 Agent
  实例）→ 归 Context；step（循环内步数）才是【循环生命周期】→ 留循环局部变量

### 3. SubagentContext 退化（子 agent 专属配置层）

```python
class SubagentContext(AgentContext):
    """子 agent 运行环境 = 统一 AgentContext + 子专属配置。"""

    def __init__(self, *, goal, context, write_paths=None, timeout=120.0, **kw):
        super().__init__(goal=goal, parent=PARENT_MARKER, **kw)
        self.write_paths = write_paths or []
        self.timeout = timeout
```

`parent` 指向主 agent 的 context（主 agent 内部创建子 agent 时传入），
`write_paths` 是子专属授权清单（继承自统一 Context 的安全边界）。

### 4. 控制面通用化（最大收益）

```
统一后，steer/stop/poll 对【任何 agent】可用：
  子 agent：父 agent 通过 SubagentManager.steer/stop（现状，改调 context 方法）
  主 agent：用户/CLI 也能 steer/stop（未来）——
    CLI /stop 命令 = 主 agent context.stop()！同一套机制！

SubagentManager 变成"通用控制台"：
  它不再持有 SubagentContext 专属逻辑，而是操作统一的 AgentContext
  （sessions 注册表 → contexts 注册表，任何 agent 都可注册）
```

### 5. session 系统接入点统一（v2）

```
未来持久化层只认 AgentContext（persist 字段）：
  persist=True（主）→ 对话历史落盘
  persist=False（子默认）→ 瞬态（审计可显式开）
  ——一个接入点，不问主/子
```

## 迁移清单

| 文件 | 改动 |
|---|---|
| `qi_agent/context/context.py`（新） | AgentContext + ContextStatus（从 subagent.py 提取通用部分） |
| `qi_agent/agent.py` | `context` 参数（组合持有）；`self.events`/`max_turns` 改从 context 取（向后兼容） |
| `qi_agent/subagent.py` | SubagentContext 继承 AgentContext（加 write_paths/timeout）；SubagentManager 改操作 context 方法 |
| `qi_agent/tools/builtin/delegate_task.py` | `_ContextAdapter` 适配新接口（同步模式） |
| 测试 | test_subagent_phase3/4 适配（manager.contexts）；新增 AgentContext 单元测试 |

## 决策点

| # | 决策 | 选项 | 倾向 |
|---|---|---|---|
| D1 | 主 agent 是否本轮就接入 Context | A. 接入（组合持有，不动循环） B. 只定义不接入（v2 再接） | **A（用户拍板）**——引用面小，一步到位 |
| D2 | 消息历史归属 | A. 迁入 Context B. 留 Agent | **A（用户拍板）**——session/记忆接入点是数据载体不是执行者；subagent 实例销毁但 Context 在，可归档持久化；无状态 Agent 可被新实例接管（断线续聊基础） |
| D3 | `_turn`/`_usage` 归属 | A. 迁入 Context B. 留 Agent | **A（用户拍板）**——会话生命周期状态（跨 chat/跨 Agent 实例）归会话载体；外部读取方（cli/runner/delegate_task）走兼容委托 |
| D4 | SubagentContext 形态 | A. 继承 AgentContext B. 组合 AgentContext | **A（用户拍板）**——子专属字段少，继承最简 |
| D5 | SubagentManager 改造 | A. 操作统一 Context B. 保留 SubagentContext 引用 | **A（用户拍板）**——通用控制台（为未来主 agent 控制铺路） |
| D6 | 兼容层 | A. 不留（一次性迁移） B. 留别名 | **A（用户拍板）**——一次性迁移；但外部读取方（cli/runner/delegate_task）保留方法名做委托（避免全改调用方，非兼容层是薄委托） |

## 工业级落地考量（P0-9）

- **可靠性**：控制面（steer/stop）用 threading.Event（已有）；主 agent 接入后
  用户可终止失控循环（止损能力）——比现状（只能等 max_turns）更强。
  裁剪：崩溃恢复依赖 session 系统（v2），本轮不做 checkpoint。
- **安全**：权限边界不变（受限子集 + 授权清单 + 审批链）；控制面通用化
  不放大权限（stop/steer 是控制不是提权）。审计：事件总线不变。
- **记忆**：本轮只统一抽象，持久化仍归 session 系统（v2）——persist 字段
  先定义不实现（诚实标注：字段就位，落盘 v2）。
- **可观测**：事件流不变（agent/* 命名空间）；context 状态可查询
  （poll 对主 agent 也开放——CLI /context 未来可显示运行状态）。
- **取舍声明**：主 agent 的 steer/stop 控制面本轮【只做机制不做调用者】
  （CLI /stop 等 v2 接线——与 subagent manager 同款 YAGNI）。

## 验收标准

1. 全量测试绿（现有 464 + 新增 AgentContext 单测）
2. 主 agent 接入后行为不变（chat 循环/事件/消息回填零变化）
3. SubagentContext 继承后 subagent 全部功能不变（steer/stop/poll/wait）
4. SubagentManager 改操作统一 Context 后测试全绿
5. ruff 全过
