# run_python 软沙箱工具（v1）技术方案（待评审）

> **状态:** 待用户评审
> **作者:** Hermes（qi-agent 开发会话）
> **日期:** 2026-08-17
> **前置:** v0.4.1（流式输出）+ TODO 沙箱 v1 条目
> **原理参考:** principles/05-run_python沙箱三方案原理（v1 手写白名单）

---

## 1. 目标

新增 `run_python` 工具：让 agent 能**在软沙箱中执行 Python 代码片段**（算数、处理数据、逻辑验证），同时通过"白名单 + 子进程 + 超时 + 干净环境"四层防护保证安全。

**这是 TODO 沙箱体系的第一块地基**——v2（RestrictedPython）和 v3（psutil）都在此基础上增强。

## 2. 设计原理（导师讲解：沙箱三件套 + 第四件）

回顾 principles/05：沙箱 = 权限锁 + 隔离锁 + 时间锁。本方案再加一把**安全锁**（干净环境）：

| 锁 | 防什么 | 实现 |
|----|--------|------|
| **权限锁** | 代码调用危险操作 | 静态白名单扫描（禁 import os/sys/subprocess、eval/exec、open、反射链） |
| **隔离锁** | 崩溃传染主进程 | 子进程执行（subprocess.run） |
| **时间锁** | 死循环烧资源 | 10 秒硬超时 |
| **安全锁** | 偷环境变量（API key） | 环境变量白名单（只留 PATH/SYSTEMROOT/TEMP 等） |

**为什么四把锁缺一不可：**
- 只有权限锁 → 死循环耗资源（时间锁缺失）
- 只有时间锁 → 能读 .env（安全锁缺失）
- 只有子进程 → 能删文件（权限锁缺失）

## 3. 设计

### 3.1 工具文件（1 文件 1 工具，v0.4.0 架构）

`qi_agent/tools/run_python.py`：

```python
"""run_python 工具：软沙箱执行 Python 代码（v1 手写白名单）。"""

from qi_agent.tools.registry import register

# 权限锁：静态扫描的黑名单模式
_FORBIDDEN_PATTERNS = (
    "import os", "import sys", "import subprocess", "import shutil",
    "import socket", "__import__", "eval(", "exec(", "open(",
    "().__class__", "__subclasses__", "__globals__",
)

# 安全锁：环境变量白名单（只保留 OS 运行必需）
_SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC",
                  "TEMP", "TMP", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")

_MAX_OUTPUT_CHARS = 2000
_TIMEOUT_SECONDS = 10


def _build_safe_env() -> dict:
    """构建白名单环境变量：丢弃 DEEPSEEK_API_KEY 等敏感变量。"""
    return {k: v for k, v in __import__("os").environ.items() if k in _SAFE_ENV_KEYS}


def _check_code(code: str) -> str | None:
    """静态白名单扫描。返回 None=通过，否则返回拦截原因。"""
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in code:
            return f"代码包含受限操作: {pattern}"
    return None


def run_python(code: str) -> str:
    """在软沙箱中执行 Python 代码并返回输出。

    安全设计（四锁）：
    - 权限锁：静态扫描禁 import os/sys/subprocess、eval/exec、open、反射链
    - 隔离锁：子进程执行，崩溃不影响主 agent
    - 时间锁：10 秒硬超时，防死循环
    - 安全锁：环境变量白名单，防偷 API key
    """
    import os
    import subprocess
    import sys

    # 1. 权限锁：静态扫描
    blocked = _check_code(code)
    if blocked:
        return f"[安全拦截] {blocked}"

    # 2+3+4. 子进程执行 + 超时 + 干净环境
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_build_safe_env(),
        )
        output = result.stdout or result.stderr or "(无输出)"
        if len(output) > _MAX_OUTPUT_CHARS:
            return output[:_MAX_OUTPUT_CHARS] + "\n...[输出已截断]"
        return output
    except subprocess.TimeoutExpired:
        return f"[错误] 代码执行超时（{_TIMEOUT_SECONDS}秒）"
    except OSError as exc:
        return f"[错误] 执行失败: {exc}"


register(
    name="run_python",
    toolset="builtin",
    handler=run_python,
    description="在软沙箱中执行 Python 代码片段（安全受限：禁危险操作，10秒超时）",
)
```

### 3.2 注册与导出

- `qi_agent/tools/__init__.py` 加 `from qi_agent.tools import run_python`
- `qi_agent/cli.py` 加导入（触发注册）

## 4. 测试设计（TDD）

`tests/test_run_python.py`：

| 用例 | 验证点 |
|------|--------|
| `test_execute_simple_code` | `print(1+1)` → "2" |
| `test_execute_returns_stdout` | 多行代码输出 |
| `test_block_import_os` | `import os` 被拦截 |
| `test_block_import_subprocess` | `import subprocess` 被拦截 |
| `test_block_open` | `open(...)` 被拦截 |
| `test_block_reflection` | `().__class__` 被拦截 |
| `test_block_eval_exec` | `eval(`/`exec(` 被拦截 |
| `test_timeout_infinite_loop` | `while True: pass` → 超时提示 |
| `test_safe_env_no_api_key` | 代码打印环境变量，断言不含 DEEPSEEK_API_KEY |
| `test_output_truncated` | 超长输出截断 |
| `test_registered_in_registry` | 工具已注册且 schema 正确 |

## 5. 文件变更清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `qi_agent/tools/run_python.py` | 新建 | run_python 工具（~80 行） |
| `qi_agent/tools/__init__.py` | 修改 | +run_python 导入 |
| `qi_agent/cli.py` | 修改 | +run_python 导入（触发注册） |
| `tests/test_run_python.py` | 新建 | 11 个测试 |

## 6. 实施步骤

1. TDD：写 test_run_python.py → RED → 实现 run_python.py → GREEN
2. 更新 __init__.py / cli.py 导入
3. 全量验证：pytest + ruff
4. 手工验收：--debug 对话让 agent 执行代码（如"帮我算 17*23"）
5. commit → tag v0.4.2
6. 归档：devlog + 更新 TODO（v1 打勾 ✅）

## 7. 验证标准（验收）

- [ ] `uv run pytest` 全绿（新增 ≥11 个测试）
- [ ] `uv run ruff check .` 无错误
- [ ] 手工对话：agent 能算数/处理数据（调 run_python）
- [ ] 手工验证：`import os` 被拦截、超时生效
- [ ] git tag v0.4.2 已打

## 8. 风险与说明

- **黑名单局限（诚实说明）**：v1 是"特征扫描"，可被 `"impo"+"rt os"` 拼接绕过——这是 v2（RestrictedPython AST 重写）要解决的，本阶段接受此局限
- **Windows 兼容**：subprocess.run + sys.executable 跨平台 OK；环境变量白名单已含 Windows 必需（SYSTEMROOT/WINDIR/COMSPEC）
- **执行权限**：沙箱内代码与主进程同用户权限——可读写用户文件（v1 靠黑名单拦 open，但 `import pathlib` 后 pathlib.Path().read_text() 可绕过，同属 v2 范畴）

## 9. 请评审确认的决策点

1. **run_python 工具**（软沙箱 v1：四锁设计）是否认可？
2. **黑名单范围**（os/sys/subprocess/shutil/socket/eval/exec/open/反射链）是否认可？
3. **环境变量白名单**（10 个 OS 必需变量）是否认可？
4. **10 秒超时 + 2000 字符截断** 是否认可？
5. **tag v0.4.2** 是否认可？

---

*评审通过后按本文档第 6 节实施。*
