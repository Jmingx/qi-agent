# AgentMailbox 邮局模型落地（Phase 1）

日期：2026-08-29
方案：docs/plans/2026-08-29-AgentMailbox统一通信方案.md（v2 邮局模型，用户审批通过）

## 做了什么

Phase 1 全部落地（方案验收 1/2/4/5/6）：

1. **新建 `qi_agent/agents/mailbox.py`**（邮局四件套）：
   - `Message`：邮件三要素必须实现（id=uuid4 唯一、sender、target）+ type/data/timestamp
   - `AgentMailbox`：无状态纯队列（owner_id + queue.Queue + send/drain）——
     不持有 status/result/phase（回归 context 状态机）
   - `Dispatcher`：邮局（路由表 register/unregister + send 查表投递 +
     send_direct 构造注入直投 + 未知 target fail-closed KeyError）

2. **context.py：steer_queue → queue.Queue**（线程安全修复）：
   - steer() → put（原子）；drain_steer() → get_nowait 取空；
     reset() → 重建队列
   - 消灭 copy+clear 竞态（控制线程 append vs 循环线程 clear 丢指令）

3. **subagent.py：SubagentContext 对接邮局**：
   - 增加 mailbox 属性（spawn 时由 AgentManager 创建注册）
   - 新增 drain_messages()：消费对话投递（type="message" 过滤，存储分离）

4. **agent_manager.py：邮局集成**：
   - __init__：创建 Dispatcher + 主 mailbox（owner="main"，收所有子结果）+ 注册
   - spawn()：创建子 mailbox + dispatcher.register（owner_id=session_id）
   - _run()：子任务完成 → dispatcher.send(result, target="main") 结果回传
   - 新增 send_message(session_id, text)：对话投递（多轮指导能力，验收 4）

5. **delegate_task.py：_steer_watcher 加对话消费钩子**：
   - message_hook = session.drain_messages（getattr 兜底，_ContextAdapter 返回空）
   - 每轮 pre-step：对话投递 → 追加 [父对话投递] user 消息；steer → [父补充指令]

## 怎么做的（TDD）

- 先写 tests/test_mailbox.py（16 个测试：三要素/并发零丢失/控制优先/
  fail-closed/注册路由/集成链路）→ 红 → 实现 mailbox.py → 绿
- 适配 test_concurrency_race2.py::test_steer_queue_race：
  直接摸内部实现（append/pop/len）→ 改用公开接口（steer/drain_steer），
  语义不变（验证零丢失）

## 遇到的问题与解决

1. **queue 未导入**（mailbox.py 用了 queue.Empty 但没 import + _inbox 未定义）：
   lint 语法过了但运行时炸 → 补 import + 定义
2. **context.py 缺 import queue**：steer_queue 队列化后 NameError →
   补 import
3. **test_steer_queue_race 用 len(Queue)**：queue.Queue 不支持 len() →
   测试改用公开接口（drain_steer），断言改为"生成总数 = 消费总数"
4. **PYTHONPATH 污染**：系统 python 缺 psutil → 用 .venv/Scripts/python.exe

## 验证

- tests/test_mailbox.py：16 个全绿（新增）
- 相关并发/管理测试：34 个全绿
- 全量回归：620 passed
- ruff：All checks passed

## 下一步（Phase 2/3，待评审后做）

- Phase 2：状态所有权切换（stop 信号化，循环线程转换状态）+
  跨线程 emit 收口（事件总线单线程化，通知走邮局）
- Phase 3：Transport 换后端（JSON-RPC/socket——接口已抽象，独立 TODO）
- 审批表 uuid+锁（线程安全方案 3.2，独立保留）
