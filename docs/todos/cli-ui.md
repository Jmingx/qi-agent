# CLI/UI 交互升级 TODO（阶段 2+）

> 演进路径：CLI(print REPL) → TUI(prompt_toolkit) → Web(gateway)
> 原则：**数据层（usage/事件点）先行，界面层逐级替换，agent 核心零改动**
> 预研来源：docs/plans/2026-08-20-资源监控插件方案.md 附录

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **/stop 实时中断（Phase A：F+pool 替换）** | P0 | ⭐⭐⭐ | 方案 docs/plans/2026-08-24-stop实时中断方案.md（待评审）。当前 /stop 下轮生效（等 LLM 返回最长 60s）→ 改为：manager.run 后台线程跑 LLM + 双事件等待（stop/done）→ stop 立即返回"已中断" + pool.release 旧 agent（新请求新 agent 接管同一 context，无状态替换） |
| ⬜ | **D3 掐 socket（Phase B）** | P1 | ⭐⭐⭐ | 真请求级取消（省 token）：force_close_tcp_sockets 掐断 httpx 活跃连接。方案第 4 节详细设计（Hermes #29507 FD 安全：线程间只 shutdown 不 close；独立 client 防误伤并发请求；_request_cancelled 防误读网络 bug） |
| ⬜ | **Ctrl+C 双语义** | P1 | ⭐⭐ | 第一次中断当前任务（回输入提示符继续聊），第二次退出。SIGINT handler 设 stop 标志（与 /stop 统一机制）。方案第 3 节 |
| ⬜ | **记忆整合（consolidation）** | P1 | ⭐⭐⭐ | 记忆超限的业界处理（Hermes 源码实证：超限不截断——返回错误引导 agent 用 replace/remove 智能整合；程序检测 + agent 判断哪些合并/删除；失败降级 N 次停止）。**我们现状**：add_memory 超限硬截断（rendered[:limit]——撕碎条目，程序盲删）。**改法**：去掉硬截断 + 超限返回信号（用量/条目列表）+ /forget 删条目 + v2 agent 自动整合（LLM 分析 → replace/remove）。**对齐**：Hermes consolidation / DSH agent 整理 / CC 提示手动整理 |
| ⬜ | **记忆 Context 化（用户提出，暂缓）** | P1 | ⭐⭐⭐ | 把记忆做成像 agent 一样的 Context（数据载体）统一管理：MemoryContext（记忆数据载体）复用 AgentContext 哲学（id/persist/status）+ AgentManager 统一管理 + 可搜索/压缩/接管。**评估**：方向对（记忆数据载体化），但当前记忆简单（几十条分节）场景账不足；且记忆是"知识条目"非"对话消息"，不能完全照搬 AgentContext。**触发**：记忆复杂化（搜索/压缩/多 agent 共享）时做。底层存储保持 Markdown（可读性拍板） |
| ⬜ | **CLI 交互升级：prompt_toolkit TUI** | P1 | ⭐⭐⭐ | 状态栏固定底部（token/上下文/权限模式实时刷新）、多行输入、历史补全——Hermes 路线（Application + layout status bar + refresh_interval + erase_when_done + resize 处理，源码实测）。CLI 从 print REPL 重构为 Application 事件循环，新依赖 prompt_toolkit。**资源监控阶段 1 的 usage 数据层是此阶段地基** |
| ⬜ | **Web 终端（网关模式）** | P2 | ⭐⭐⭐⭐⭐ | Hermes gateway 架构参考：agent 核心 + WebSocket 界面（浏览器/手机访问）。网关服务（会话管理/并发/认证）是最大工程。事件点+插件天然跨界面——展示层替换（print → TUI → WebSocket 推送），agent 核心零改动 |
| ⬜ | **上下文窗口模型化** | P1 | ⭐⭐ | 资源监控遗留（用户评审提出）：上限写死 64000 不够灵活——每个模型有固定窗口。**模型→窗口大小映射表**（MODEL_CONTEXT_LIMITS：deepseek-chat/reasoner 等），从当前模型名查询，查不到回落 QI_CONTEXT_LIMIT/默认值（Hermes models registry 同款思路）；探测 API 返回的 max context（如有） |
