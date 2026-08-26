# AgentPool + 运行时/执行者分离方案

> 日期：2026-08-24
> 状态：待评审
> 关联：`2026-08-24-AgentManager统一控制台方案.md`（已实施：控制台 + 两级状态机）、
>       `2026-08-24-AgentContext统一合并方案.md`（已实施：数据载体）、
>       `2026-08-23-subagent方案.md`（subagent，pool 的受限特例）

## 1. 背景与目标

当前 build_agent 一次性创建 manager + context + agent（三者绑定返回）。
用户拍板方向：**build_agent 拆成 build_runtime + make_agent 两个工厂，
延迟创建 agent；并实现 AgentPool 按需提取执行者（支持并行调用）**。

场景账（用户评审确认）：
- **agent 复用是伪需求**——Agent 无状态 + make_agent 极轻（无网络 I/O，
  仅配置装配）→ 复用维护池状态得不偿失
- **并发上限是真实需求**——多主对话（v2）/ team 模式（v2+）都要受控
  （防同时 N 个会话烧 token）
- **max_concurrent 已有雏形但未生效**（SubagentManager 存着没用）

## 2. 核心设计：运行时与执行者分离

```
build_runtime() → manager + context（运行时，长期存活）
  —— 只建一次，会话在它在（可恢复/可持久化）

make_agent(context, type="standard") → 执行者工厂（可插拔）
  —— 按需创建 Agent（换实现 = 换工厂参数/注册表）
  —— 轻量（无网络，仅装配）

AgentPool = 轻壳（工厂 + 并发治理，不复用）：
  acquire() → 检查 max_workers → make_agent → 跑任务 → 完成即弃
  —— 池不是"复用池"（agent 轻，复用无收益）
  —— 池是"并发信号量 + 工厂"（上限治理 + 统一派活）
```

**为什么池不复用（用户评审拍板）**：
```
make_agent ≈ 微秒级（配置装配，无 I/O）
维护池状态（借出/归还/健康检查）复杂度 >> 省下的构造开销
→ 池 = Semaphore（限并发）+ Factory（创建），不是 ThreadPool 式复用
类比（Java）：Executors 的工厂 + 信号量，不是线程池的 worker 复用
（线程重才复用；agent 轻，即建即用）
```

## 3. 场景矩阵（两种 pool）

```
主对话池（一个 context 一个 agent，串行）：
  CLI：build_runtime() + make_agent（主对话执行者）
  ——本质是"执行者工厂"，不需要并发治理（用户一次一句话）
  ——但为统一，也走 pool.acquire（拿"主对话执行者"）

子任务池（多 context 多 agent，并行）：
  SubagentManager.spawn → pool.acquire（多个 worker 并行）
  ——并发治理在这里生效（max_workers 控制 worker 数）
  ——各 worker 用独立 context（隔离，对齐 subagent 哲学）

演进：SubagentManager 内部改走 AgentPool（统一派活）
  subagent = pool 的受限特例（工具子集 + 授权清单）
```

## 4. 方案设计（3 个 Phase）

### Phase 1：build_agent 拆分（build_runtime + make_agent）

```python
# qi_agent/agents/factory.py
def build_runtime(debug=False, stats=False, interactive=True,
                  plugin_overrides=None) -> RuntimeBundle:
    """运行时（长期存活）：manager + context + 插件装配。
    不创建执行者（agent）——延迟注入。"""
    events = EventBus()
    context = AgentContext(persist=True, events=events)
    manager = AgentManager()
    agent_id = manager.register(context, role="main")
    installed = load_plugins(events, plugin_config)
    return RuntimeBundle(manager=manager, context_id=context.id,
                         agent_id=agent_id, installed=installed)

def make_agent(context: AgentContext, type: str = "standard") -> Agent:
    """执行者工厂（可插拔）：按需创建 Agent。
    换实现 = 换 type（注册表）或换工厂——context 数据载体不变。"""
    # type="standard" → Agent(LLMClient(api_key), PROD_SYSTEM_PROMPT, context)
    ...

# build_agent 保留（兼容：build_runtime + make_agent 的快捷组合，
# 评测/旧调用零改动）
```

### Phase 2：AgentPool（轻壳：工厂 + 并发治理）

```python
# qi_agent/agents/pool.py
class AgentPool:
    def __init__(self, manager: AgentManager, max_workers: int = 3):
        self.manager = manager
        self.max_workers = max_workers
        self._active = 0
        self._lock = threading.Lock()

    def acquire(self, context: AgentContext | None = None,
                type: str = "standard") -> Agent:
        """取执行者（检查并发上限，超限等待）。context None = 新建子任务 context。
        完成即弃（不复用——make_agent 轻，复用无收益）。"""
        with self._lock:
            while self._active >= self.max_workers:
                self._lock.release()
                time.sleep(0.05)
                self._lock.acquire()
            self._active += 1
        try:
            agent = make_agent(context, type=type)
            if context is None:
                # 子任务 context：注册进 manager + 返回（agent + ctx）
                ...
            return agent
        except Exception:
            with self._lock:
                self._active -= 1
            raise

    def release(self, agent: Agent) -> None:
        """任务完成，归还额度（agent 即弃）。"""
        with self._lock:
            self._active -= 1
```

### Phase 3：SubagentManager 演进（走 pool 统一派活）

```python
# SubagentManager.spawn 内部改走 AgentPool.acquire
# （工具子集 + 授权清单作为 type 参数/工厂参数——subagent 是受限特例）
# max_concurrent 从"存着没用"变为真正生效（pool.max_workers）
```

## 5. 决策点

| # | 决策 | 选项 | 倾向 |
|---|---|---|---|
| D1 | build_agent 保留兼容 | A. 保留（= build_runtime + make_agent 快捷组合） B. 删除（调用点全改） | **B（用户拍板）**——彻底拆分不留兼容：cli.py + runner.py 全改走 build_runtime + make_agent；测试 mock 同步适配 |
| D2 | AgentPool 是否复用 | A. 不复用（轻壳：工厂+上限） B. 复用（归还池） | **A（用户拍板）**——make_agent 轻，复用无收益 |
| D3 | pool.acquire 并发超限 | A. 等待（阻塞） B. 拒绝（抛异常） | **A**——等待更友好（任务会完成），拒绝要调用方重试 |
| D4 | SubagentManager 演进 | A. 内部走 pool B. 并存 | **A（用户拍板）**——统一派活，max_concurrent 真正生效 |
| D5 | 多主对话支持 | A. 本方案只做机制（build_runtime 可多次调用=多会话） B. 实现会话管理 | **A**——机制就位（每次 build_runtime 一个会话），会话管理 v2 |
| D6 | 执行者类型注册表 | A. type 参数 + 简单映射 B. 注册表（可扩展） | **A**——v1 就 standard，映射够了（对齐 tools registry 哲学 v2 再扩） |

## 6. 工业级落地考量（P0-9）

- **可靠性**：acquire 超限等待（不失败）；release 用 try/finally 保证
  额度回收（异常也不泄漏并发额度）；崩溃恢复依赖 context 持久化（v2）
- **安全**：执行者工厂只创建受信类型（type 映射白名单）；子任务 context
  隔离（并行任务各用各的，不共享）；审计事件沿用 manager.register
- **记忆**：运行时（manager/context）长期存活 = 会话持久化的承载点；
  执行者即建即用不落盘（无状态，无持久化价值）
- **可观测**：pool 活跃数可查（_active）+ acquire/release 可发事件
  （agent-pool/acquire、agent-pool/release——未来 /status 显示并发占用）
- **取舍声明**：本方案只做"运行时/执行者分离 + 并发治理"；
  多主对话会话管理（D5）、team 编排（v2+）、执行者类型注册表（D6）
  均为后续增量——场景账：当前单用户 CLI 主对话串行是本质，池的价值
  主要在子任务并行（已有 subagent 场景）

## 7. 验收标准

1. build_runtime 返回 manager + context_id（不创建 agent）
2. make_agent(context) 创建执行者（LLMClient + PROD_SYSTEM_PROMPT 装配）
3. build_agent 保留兼容（= build_runtime + make_agent 快捷组合）——评测/旧测试零改动
4. AgentPool.acquire 并发超限等待 + release 回收额度（try/finally）
5. SubagentManager.spawn 走 pool（max_concurrent 真正生效）
6. 全量回归绿 + ruff 过
