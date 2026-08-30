# devlog: AgentMailbox 中央队列演进（v3——异步消息代理 + A2A 预留）

日期：2026-08-29
方案：docs/plans/2026-08-29-AgentMailbox中央队列演进方案.md（用户审批通过）

## 做了什么（Phase 1 全部落地）

1. **mailbox.py v3 重构**：
   - `AgentMailbox` 双队列语义：outbox（→central 引用）+ inbox
     - outbox 不是独立队列——register 时绑定 central（用户拍板）
     - send = 丢 outbox = 丢 central（一步入中央——零两步/中间层）
     - 未注册 send → RuntimeError（fail-fast）
   - `Dispatcher` 升级为独立 daemon 线程（消息代理）：
     - 中央队列（所有消息汇聚——阻塞 get 零轮询 + FIFO 顺序）
     - run()：central.get → _deliver（查路由 → 目标 inbox）
     - _deliver 路由决策：本地 routes / 远程 foreign（A2A 预留）/
       fail-closed（undeliverable 审计——不静默丢信）
     - stop() 优雅关停（stop 事件 + join 超时）
   - `send_direct` 保留：同步直投（免中央/免查表——父↔子已知引用）
   - `ForeignMailbox` Protocol：A2A 对外通道接口（Phase 2 实现）

2. **agent_manager.py 适配**：
   - Dispatcher 构造后 start()（启动搬运线程）
   - 主 mailbox 注册（绑定 central）+ 子 mailbox spawn 时注册

## 语义变化（v2 → v3）

| 项 | v2（同步） | v3（异步） |
|---|---|---|
| Dispatcher.send | 同步查表投递 | 丢中央（Dispatcher 线程异步搬运） |
| 未知 target | 同步抛 KeyError | 异步审计 undeliverable（不阻塞调用方） |
| send_direct | 免查表直投 | 保留（同步直投 inbox） |
| 消费方 | 投递即到 | 投递后等搬运（异步延迟） |

## 遇到的问题与解决

1. **测试时序竞态**（send 后立即 drain——Dispatcher 还没搬完）：
   → _wait_for 轮询 helper（等 inbox.qsize）——测试适配异步语义
2. **v2 测试断言同步抛错**（KeyError）→ v3 异步审计：
   → 改断言 undeliverable 留痕
3. **裸 mailbox send**（未注册）→ RuntimeError（v3 新行为 fail-fast）：
   → 测试用 Dispatcher 绑定 或 send_direct 直投
4. **Dispatcher 没 start 就不搬运**：测试要显式 start

## 验证

- tests/test_mailbox.py：18 全绿（+未注册 send fail-fast 测试）
- 全量回归：625 passed + ruff 全过

## 下一步

- Phase 2（A2A——独立 TODO，场景账：本地暂无远程）：
  target 三元组（ip:port:agent_id）+ ForeignMailbox 实现
- 状态所有权切换（线程安全方案 Phase 2——stop 信号化）
- 跨线程 emit 收口（事件总线单线程化——通知走邮局）
