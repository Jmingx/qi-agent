# run_python 审批档方案：沙箱降级走审批（环境变量开关退役）

> **状态:** ✅ 已评审通过（2026-08-21，决策点 1-4 用户整体批准）
> **作者:** Hermes（qi-agent 开发会话）
> **日期:** 2026-08-21
> **前置:** v0.4.22；TODO P0（docs/todos/tool-calling.md：沙箱降级需用户审核）
> **背景:** 沙箱 legacy 降级当前是"环境变量显式开关"（过渡方案）——静默全局降级
> 有 v1 绕过风险；审批机制已就绪（v0.4.18 agent/tool-approval + approval_gate）

---

## 1. 问题

| 现状 | 风险 |
|------|------|
| `QI_SANDBOX_MODE=legacy` 环境变量全局降级 | 设一次忘一次，**所有** run_python 静默走完整 Python（v1 静态扫描可被拼接绕过）——用户无感知 |
| 降级无弹窗无审批 | 与 shell 三档权限不对称（shell 危险命令要弹窗，run_python 降级却不用） |

**目标**：降级成为**逐次审批**操作（弹窗确认"降级沙箱安全等级？"），环境变量开关退役，与 shell 三档对齐。

## 2. 设计

### 2.1 run_python 加 approved 内部参数（对齐 shell）

```python
def run_python(code: str, approved: bool = False) -> str:
    """
    approved=False（默认）：restricted 受限执行（现状，最安全）
    approved=True（用户审批后 agent 内部注入，模型 schema 不可见）：
        该次调用走 legacy（完整 Python，v1 静态扫描跳过）——逐次降级
    """
```

- **安全底线**：模型无法传 approved（schema 不暴露，参数校验拒绝多余参数）——
  **模型永远无法自主降级**，降级只发生在用户弹窗批准之后
- `approved=True` 跳过 v1 静态扫描（对齐 shell：approved 跳过白名单/危险关键词）——
  用户批准降级 = 用户承担该次代码的风险
- **`QI_SANDBOX_MODE` 环境变量退役**：模块级读取逻辑删除，restricted 是唯一默认

### 2.2 security_guard 判据：import 白名单外 → NEED_APPROVAL

```python
# 受限环境可 import 的模块（对齐 _sandbox_runner._ALLOWED_EXTRA_MODULES）
_RESTRICTED_MODULES = {"math", "random", "json", "statistics", "fractions", "decimal"}

def _needs_sandbox_downgrade(code: str) -> str | None:
    """检测 run_python 代码是否需要降级：import 白名单外模块。"""
    for m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M):
        module = m.group(1).split(".")[0]
        if module not in _RESTRICTED_MODULES:
            return f"代码需要 import '{module}'（受限环境白名单外）"
    return None
```

- 命中 → `NEED_APPROVAL:沙箱降级:<说明>`（复用现有审批链路，agent 发
  agent/tool-approval 事件 → approval_gate 弹窗）
- `import os/sys/subprocess` 等同样触发降级审批（v1 黑名单在 approved 时被跳过，
  但用户批准前不会执行）——**比现在更安全**（现在 legacy 静默执行）
- 未命中 → 放行 restricted（零侵入，普通代码不受影响）

### 2.3 approval_gate 弹窗文案扩展（按工具名）

```python
if name == "run_python":
    prompt = f"[审批] 降级沙箱安全等级（完整 Python 执行）？代码: {code[:50]}... (y=同意 / n=拒绝) "
else:  # shell 等：现状
    prompt = f"[审批] 执行命令 '{command}'？(y=同意 / n=拒绝 / a=总是允许) "
```

**决策点 3**：run_python 降级**不提供 a=总是允许**——代码千变万化，前缀记忆无意义，
且"总是允许降级"= 变相恢复环境变量全局降级（安全倒退）。逐次确认。

### 2.4 评测适配（自动覆盖，无需改）

- evaluation 用 `build_agent(interactive=False)` → approval_gate 不装配 →
  agent/tool-approval 无监听器 → bail 返回 None → **fail-closed 拒绝**（approved 非
  True 不注入）→ 评测中需要降级的代码被拒（安全任务断言此行为，同 s5）

## 3. 安全分析

| 攻击面 | 防护 |
|--------|------|
| 模型传 approved 自主降级 | schema 不暴露 + 参数校验拒绝多余参数（approved 是 execute_tool 调用级 internal） |
| 环境变量静默降级 | `QI_SANDBOX_MODE` 读取删除——restricted 唯一默认 |
| 降级常态化（每次弹窗都点同意） | 用户逐次决策；a=总是允许 对 run_python 禁用 |
| 评测环境降级绕过 | interactive=False → approval_gate 不装配 → fail-closed |

## 4. 测试设计（TDD）

| 用例 | 验证点 |
|------|--------|
| `test_sandbox_downgrade_needs_approval` | security_guard 对 `import requests` 类代码返回 NEED_APPROVAL:沙箱降级 |
| `test_sandbox_approved_runs_legacy` | `run_python(code, approved=True)` 跳过 v1 走 legacy（`import sys` 可执行） |
| `test_sandbox_no_approval_stays_restricted` | 普通代码（无 import 白名单外）不触发审批，restricted 照跑 |
| `test_sandbox_approved_not_in_schema` | run_python schema 只暴露 code，approved 隐藏 |
| `test_sandbox_approval_flow` | security_guard + approval_gate 同挂：import 白名单外 → 弹窗 → 同意 → legacy 执行 |
| `test_sandbox_mode_env_retired` | QI_SANDBOX_MODE 环境变量不再影响执行模式 |
| 现有沙箱/审批测试 | 回归 |

## 5. 文件变更

| 文件 | 操作 |
|------|------|
| `qi_agent/tools/run_python.py` | +approved 参数 +legacy 分支 +删 QI_SANDBOX_MODE +schema 手写（~30 行） |
| `qi_agent/plugins/security_guard.py` | +_RESTRICTED_MODULES +_needs_sandbox_downgrade 判据（~20 行） |
| `qi_agent/plugins/approval_gate.py` | 弹窗文案按工具名分支（run_python 专用） |
| `tests/test_run_python*.py` / `tests/test_security_guard.py` / `tests/test_approval.py` | +6 用例 |
| `docs/plans/2026-08-21-run_python审批档方案.md` | 本方案 |

## 6. 决策点（评审）

| # | 决策点 | 方案 |
|---|--------|------|
| 1 | 降级触发判据 | 静态检测 import 白名单外模块（对齐受限白名单）——非 import 的受限操作（如 pathlib 文件访问）检测复杂，留 TODO |
| 2 | approved 语义 | 对齐 shell：用户批准后 agent 内部注入，该次调用走 legacy + 跳过 v1（用户承担该次风险） |
| 3 | a=总是允许 | **run_python 降级不提供**（代码无前缀可记，且总允许=变相全局降级）；shell 保持现状 |
| 4 | 环境变量退役 | `QI_SANDBOX_MODE` 读取删除；restricted 唯一默认 |
