# devlog: AgentPool 运行时/执行者分离

日期：2026-08-24
方案：docs/plans/2026-08-24-AgentPool方案.md（用户评审通过，D1=B 彻底拆分）

## 做了什么

1. **build_runtime + make_agent 拆分**（Phase 1）：build_agent 删除（D1=B），
   RuntimeBundle（manager/context_id/agent_id/installed，不含 agent）；
   make_agent(context, type) 执行者工厂（延迟创建，可插拔）
2. **AgentPool**（Phase 2）：轻壳（工厂 + 并发治理，不复用）——
   acquire（超限等待）/ release（try/finally 回收）/ active_count 可观测
3. **SubagentManager 演进**（Phase 3）：AgentManager 持有 pool，
   _run 里 acquire/release——max_concurrent 真正生效
4. **调用点全改**：cli.py（build_runtime + make_agent）+ evaluation/runner.py
   + 所有测试 mock（build_runtime/make_agent 双层 patch）

## 遇到的问题与解决

| 问题 | 解决 |
|---|---|
| pool → factory → agent_manager → pool 循环导入 | pool 不模块级 import make_agent（acquire 内延迟） |
| build_runtime 里 api_key 未用（ruff F841） | 改为 load_api_key() 直接调用（校验 key 存在） |
| 测试 mock 巨型 lambda 长行（E501） | _fake_runtime helper 提取（假 RuntimeBundle 集中） |
| import 名被误改（load_report vs load_last_report） | 对照 report.py 实际导出修复 |
| eval 测试 make_agent 未 mock | 双层 patch（build_runtime + make_agent） |
| LLMClient mock 传类而非实例（验收脚本） | lambda key: SlowFake() |

## 验证

- 539 全绿（+5 pool 测试）+ ruff 全过
- 手工验收：并行 2 任务各 0.3s → 总耗时 0.30s（串行 0.6s）实锤并行
- build_runtime 不含 agent + make_agent 延迟创建 + 同一 context 可被新执行者接管

## 下一步（v2+）

- 多主对话（build_runtime 每会话一次）+ 会话管理
- team 模式（pool 并发治理已就位）
- 执行者类型注册表（make_agent type 扩展）
