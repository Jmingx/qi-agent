# CLI/UI 交互升级 TODO（阶段 2+）

> 演进路径：CLI(print REPL) → TUI(prompt_toolkit) → Web(gateway)
> 原则：**数据层（usage/事件点）先行，界面层逐级替换，agent 核心零改动**
> 预研来源：docs/plans/2026-08-20-资源监控插件方案.md 附录

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **CLI 交互升级：prompt_toolkit TUI** | P1 | ⭐⭐⭐ | 状态栏固定底部（token/上下文/权限模式实时刷新）、多行输入、历史补全——Hermes 路线（Application + layout status bar + refresh_interval + erase_when_done + resize 处理，源码实测）。CLI 从 print REPL 重构为 Application 事件循环，新依赖 prompt_toolkit。**资源监控阶段 1 的 usage 数据层是此阶段地基** |
| ⬜ | **Web 终端（网关模式）** | P2 | ⭐⭐⭐⭐⭐ | Hermes gateway 架构参考：agent 核心 + WebSocket 界面（浏览器/手机访问）。网关服务（会话管理/并发/认证）是最大工程。事件点+插件天然跨界面——展示层替换（print → TUI → WebSocket 推送），agent 核心零改动 |
| ⬜ | **上下文窗口模型化** | P1 | ⭐⭐ | 资源监控遗留（用户评审提出）：上限写死 64000 不够灵活——每个模型有固定窗口。**模型→窗口大小映射表**（MODEL_CONTEXT_LIMITS：deepseek-chat/reasoner 等），从当前模型名查询，查不到回落 QI_CONTEXT_LIMIT/默认值（Hermes models registry 同款思路）；探测 API 返回的 max context（如有） |
