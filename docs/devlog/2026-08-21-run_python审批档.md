# 2026-08-21 run_python 审批档（沙箱降级走审批）

## 需求

TODO P0（docs/todos/tool-calling.md）：软沙箱 legacy 降级当前是"环境变量显式开关"
（QI_SANDBOX_MODE=legacy 全局静默降级，有 v1 绕过风险）——改为逐次审批，与
shell 三档权限对齐。

## 方案（docs/plans/2026-08-21-run_python审批档方案.md，决策点 1-4 批准）

```
模型写代码 import requests（受限白名单外）
  → security_guard 判 NEED_APPROVAL:代码需要 import 'requests'...
  → agent 发审批事件 → approval_gate 弹窗"降级沙箱安全等级？(y/n)"
  → 用户同意 → agent 注入 approved（internal）→ 该次走完整 Python
  → 用户拒绝 → [审批拒绝]，模型换方案
```

关键设计：
1. **approved 内部参数**（对齐 shell）：schema 只暴露 code——模型传 approved
   会被参数校验拒绝（`[参数错误] 未知参数: approved`）→ **模型无法自主降级**
2. **security_guard 降级判据**：`_needs_sandbox_downgrade` 检测 import 白名单外
   模块（对齐受限环境 `_ALLOWED_EXTRA_MODULES`）→ NEED_APPROVAL
3. **QI_SANDBOX_MODE 环境变量退役**：模块级读取删除，restricted 唯一默认
4. **a=总是允许 对 run_python 禁用**（用户决策点 3）：代码千变万化无前缀可记，
   且总允许降级=变相恢复全局降级——逐次确认
5. **approved=True 跳过 v1 静态扫描**（对齐 shell）：用户批准承担该次代码风险

## 验证

- 全量 **255 测试全绿 + ruff 零错误**
- 手工验收五条路径：判档 ✓ 普通代码放行 ✓ 模型传 approved 被拒（防绕过）✓
  审批注入走 legacy ✓ 默认 v1 拦截 ✓
- 既有隔离测试（legacy cwd 隔离）改用 approved 触发，语义不变

## 遗留

- 降级判据只覆盖 import 白名单外模块——pathlib 文件访问等非 import 受限操作
  检测复杂，留 TODO（评测可覆盖）
- 沙箱 P2：Job Objects / Docker / 远程（原样保留）
