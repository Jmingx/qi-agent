# devlog: AgentManager 统一控制台 + agents 包归类

日期：2026-08-24
方案：docs/plans/2026-08-24-AgentManager统一控制台方案.md（用户评审通过）

## 做了什么

### AgentManager 统一控制台（方案主体）
1. **两级状态机**：ContextStatus 扩展 IDLE + ChatPhase 新增 + 转移方法
   （begin_chat/enter_*/complete_chat/fail_chat）+ 状态转移图（mermaid）
2. **AgentManager**：register 主/子 agent + spawn/steer/stop/poll/unregister；
   SubagentManager 退化为子类（接口兼容）
3. **chat 循环控制面**：should_stop 中断（下轮生效）+ 状态机更新点
4. **build_agent 接入**：AgentBundle（agent/manager/agent_id/installed）
   + CLI /status /stop 命令
5. **评测适配**：runner 解包 AgentBundle + 测试适配

### agents 包归类（用户追加拍板）
6. **执行者家族收敛**：qi_agent/agents/（agent/agent_manager/subagent/factory）
   ——可插拔边界（换执行者=本包加文件），context/events/llm 不动
   ——import 全量迁移（29 文件脚本批量替换），528 全绿零回归

## 遇到的问题与解决

| 问题 | 解决 |
|---|---|
| SubagentContext 初始 IDLE 破坏 subagent | spawn 语义=立即运行 → begin_chat() |
| stop 中断后出口 complete_chat 覆盖 STOPPED | chat 出口 if not should_stop() |
| steer 主 agent（IDLE）被 RUNNING 限制拒绝 | 放宽为"存在即可排队" |
| agent_manager ↔ subagent 循环导入 | spawn 内延迟 import |
| CLI/runner 测试 mock 返回 tuple | 改 AgentBundle 形态 |
| 文档编号撞号（20 记忆系统） | 原理 24 号 |
| git mv 未跟踪文件失败 | 普通 mv（未提交文件） |

## 验证

- 528 全绿（+32 新测试）+ ruff 全过
- agents 包导出验证（Agent/AgentManager/SubagentManager 继承关系）
- build_agent 真实装配 + 主 agent 注册验证

## 下一步（v2，已记 TODO）

- D3 升级：后台线程/信号实时 /stop
- /steer CLI 命令 + persist 落盘（会话持久化）
