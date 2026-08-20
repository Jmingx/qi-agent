# write_file 工具方案：agent 读写能力（待评审）

> **状态:** 待用户评审
> **作者:** Hermes（qi-agent 开发会话）
> **日期:** 2026-08-20
> **前置:** v0.4.18（shell 三档权限+审批机制；write 复用同一套机制）
> **TODO:** 工具调用主线新增（agent 从只读走向读写）
> **核心:** write_file 工具 + 四档路径判定（敏感拒/项目内新增自动/覆盖审批/项目外审批），复用 path_security + approval_gate

---

## 1. 目标

新增 write_file 工具：agent 支持写文件（项目内写代码/配置）。安全设计是核心——
**写比读危险一个量级**（可覆盖 .env 偷密钥、写 .git 注入 hook、写启动脚本持久化）。

## 2. 设计原理（导师讲解）

### 2.1 为什么写文件需要特别设计

```
读文件：只泄露信息（path_security 已防）
写文件：修改系统状态——
  · 覆盖 .env          → 改 API key（直接偷密钥）
  · 写 .git/config     → 注入 git hook（持久化后门）
  · 写启动脚本/计划任务 → 持久化恶意代码
```

**写 = 读 + 修改能力**。业界对照：Claude Code Write/Edit 项目内默认允许
（acceptEdits 全自动）、项目外/危险路径才问；Hermes write + path_security + approval。

### 2.2 四档路径判定（用户评审决策 1/3/4 整合）

| 档位 | 场景 | 行为 | 判定方 |
|------|------|------|--------|
| ① | 特殊路径（.env/.git/密钥文件，**全局**） | **拒绝**（红线，不可审批） | 工具层+插件层（is_sensitive_path） |
| ② | 项目内 + 新增文件 | **自动放行** | 插件层返回 None |
| ③ | 项目内 + 覆盖已有文件 | **审批**（弹窗） | NEED_APPROVAL |
| ④ | 项目外普通路径 | **审批**（弹窗） | NEED_APPROVAL |

**红线优先**：① 永远先于 ②③④ 判定（.env 写入无论在哪都拒绝，不提供审批路径）。

### 2.3 与现有体系的关系（全部复用，零新机制）

```
path_security.is_sensitive_path → ① 红线判定（读写共用）
approval_gate + agent/tool-approval → ③④ 审批（v0.4.18 机制直接复用）
approved 内部参数 → 工具层兜底（模型 schema 不可见）
security_guard._ARG_PARAM_MAP → +write_file: path（路径规则自动覆盖）
```

## 3. 设计

### 3.1 write_file 工具（qi_agent/tools/write_file.py，~60 行）

```python
"""write_file 工具：写文件（项目内 + 敏感保护 + 覆盖/越界审批）。

安全设计（方案 v0.4.19）：
- ① 敏感路径（.env/.git/密钥文件）→ 永远拒绝（红线，工具层兜底）
- ② 项目内新增 → 自动允许（agent 干活的基本能力）
- ③ 项目内覆盖已有文件 → 需 approved（审批）
- ④ 项目外普通路径 → 需 approved（审批）
- approved 为内部参数（agent 审批注入，模型 schema 不可见）
"""

import os
from pathlib import Path

from qi_agent.tools.path_security import is_sensitive_path
from qi_agent.tools.registry import register

# 项目根（写文件限定的默认范围）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _is_inside_project(path: str) -> bool:
    """路径是否在项目内（resolve 后前缀匹配，防 ../ 逃逸）。"""
    try:
        return Path(path).resolve().is_relative_to(_PROJECT_ROOT)
    except (OSError, ValueError):
        return False


def write_file(path: str, content: str, approved: bool = False) -> str:
    """写文件（UTF-8）。"""
    # ① 红线：敏感路径永远拒绝（工具层兜底，插件层也会拦）
    if is_sensitive_path(path):
        return f"[安全拦截] 禁止写入敏感路径: {path}"
    inside = _is_inside_project(path)
    exists = os.path.exists(path)
    # ③④ 覆盖/越界：需 approved（无审批插件时 fail-closed 拒绝）
    if not approved and (exists or not inside):
        reason = "覆盖已有文件" if exists else "项目外路径"
        return (
            f"[安全拦截] {reason}需用户审批，已拒绝执行。"
            f"请让用户确认后再试"
        )
    # ② 执行写入（UTF-8，对齐项目）
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path}（{len(content)} 字符）"
    except OSError as exc:
        return f"[错误] 写入失败: {exc}"


register(
    name="write_file",
    toolset="builtin",
    handler=write_file,
    description="写文件（UTF-8）：项目内新增文件自动允许；覆盖已有文件或项目外路径需用户审批",
    schema={...path/content...},  # approved 不暴露
)
```

### 3.2 security_guard 扩展（三档判定 → write_file 四档）

```python
# _ARG_PARAM_MAP 加 "write_file": "path"（路径规则自动覆盖 write）
# _on_tool_call 增加 write_file 分支：
if name == "write_file":
    path = str(arguments.get("path", ""))
    if is_sensitive_path(path):
        return f"[安全拦截] 禁止写入敏感路径: {path}"   # ① 红线
    exists = os.path.exists(path)
    if exists:
        return f"NEED_APPROVAL:覆盖写入 {path}"          # ③ 覆盖 → 审批
    if not _is_inside_project(path):
        return f"NEED_APPROVAL:项目外写入 {path}"        # ④ 越界 → 审批
    return None                                          # ② 项目内新增 → 放行
```

**注意**：security_guard 的 _check_sensitive_path 已复用（_ARG_PARAM_MAP 加
write_file→path 后自动生效）——但红线判定要在审批档**之前**（红线优先）。

### 3.3 工具层兜底 vs 插件层判定

```
插件层（security_guard）：判档（红线/审批/放行）——装配时生效
工具层（write_file 内置）：兜底（红线硬拒 + 覆盖/越界需 approved）——无插件也安全
```

**approved 内部参数**：复用 v0.4.18 机制——execute_tool(internal={"approved"})，
模型 schema 无 approved → 模型无法绕过。

### 3.4 评测适配

- 评测 interactive=False → approval_gate 不装 → 覆盖/越界 write fail-closed 拒绝 ✅
- 新评测任务（write 类）：
  - `w1`：写项目内新文件（自动放行）→ 期望 write_file 被调用 + 文件创建
  - **注意**：评测会真写文件——任务用固定文件名（如 `eval_write_test.txt`），
    评测**完成后清理**（任务定义里带 cleanup 标记或 runner 清理）
  - 覆盖/越界场景评测：fail-closed 拒绝（s 类任务，不真写）

## 4. 测试设计（TDD）

| 用例 | 验证点 |
|------|--------|
| `test_write_new_file_inside` | 项目内新增 → 写入成功（UTF-8 内容） |
| `test_write_overwrite_needs_approval` | 覆盖已有 → 无 approved 拒绝 |
| `test_write_overwrite_approved` | 覆盖 + approved=True → 写入成功 |
| `test_write_outside_project` | 项目外 → 无 approved 拒绝 |
| `test_write_sensitive_blocked` | .env/.git 路径 → [安全拦截]（即使 approved） |
| `test_write_traversal_escape` | `../` 逃逸路径 → 视为项目外（is_relative_to 防逃逸） |
| `test_security_guard_write_classify` | write_file 四档判定（红线/覆盖审批/越界审批/新增放行） |
| `test_write_approval_flow` | 集成：覆盖 → 审批同意 → 写入（FakeClient） |
| `test_write_schema` | schema 只含 path/content（无 approved） |
| `test_eval_write_task` | 评测任务 w1 定义合法性 + 清理逻辑 |

**测试注意**：
- 写入测试用 tmp_path 或项目内临时文件，teardown 清理（不污染项目）
- 项目外测试：系统临时目录（tempfile.gettempdir()）——非项目内
- 审批集成：复用 test_approval.py 的 FakeClient 模式

## 5. 文件变更清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `qi_agent/tools/write_file.py` | 新建 | 工具（~60 行） |
| `qi_agent/tools/__init__.py` | 修改 | +write_file 导入（注册） |
| `qi_agent/plugins/security_guard.py` | 修改 | _ARG_PARAM_MAP + write_file 四档判定（~15 行） |
| `qi_agent/tools/registry.py` | 无改动 | internal 机制复用 |
| `tests/test_write_file.py` | 新建 | 工具+判定+集成测试（~10 用例） |
| `evaluation/tasks.py` | 修改 | +w1 写文件任务 + cleanup 机制 |

## 6. 实施步骤

1. TDD：写测试（RED）→ 实现 write_file + security_guard 扩展（GREEN）
2. 全量验证：pytest + ruff + 手工（真实 CLI 写文件 + 覆盖审批弹窗）
3. **按新流程：展示变更 → 用户确认 → 提交**
4. 提交后：devlog + TODO 打勾 + tag v0.4.19

## 7. 验证标准（验收）

- [ ] pytest 全绿（新增 ≥10，总计 ≥210）
- [ ] ruff 无错误
- [ ] 手工：真实 CLI "帮我写个 hello.txt" → 自动写入；"覆盖 README.md" → 弹窗审批
- [ ] 评测 w1 任务通过（写文件 + 清理）
- [ ] tag v0.4.19

## 8. 风险与说明

- **写坏项目文件**：覆盖审批给了用户选择权；新增文件自动（git 可回滚）
- **路径逃逸**：`../` 相对路径——resolve + is_relative_to 防逃逸（测试覆盖）
- **编码**：UTF-8 写入对齐项目（避免 GBK 乱码）
- **评测污染**：w1 写文件后清理（cleanup 机制）——不污染项目
- **不做的**：Edit（局部修改，阶段 B）；diff 预览；二进制文件（YAGNI）

## 9. 请评审确认的决策点

1. **四档判定**（敏感拒/项目内新增自动/覆盖审批/项目外审批）是否认可？
2. **工具层兜底 + 插件层判档**（复用 approved 内部参数）是否认可？
3. **security_guard 扩展**（_ARG_PARAM_MAP + write_file 分支）是否认可？
4. **评测 w1 任务 + 清理机制**是否认可？
5. **tag v0.4.19** 是否认可？

---

*评审通过后按第 6 节实施。*
