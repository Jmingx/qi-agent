# path_security 路径安全技术方案（待评审）

> **状态:** 待用户评审
> **作者:** Hermes（qi-agent 开发会话）
> **日期:** 2026-08-17
> **前置:** v0.4.2（run_python 沙箱完成）
> **TODO:** tool-calling.md「path_security：路径安全」（P0）

---

## 1. 目标

为 `read_file`（及后续文件类工具）增加**路径安全检查**：禁止读取敏感路径（.env、.git/、密钥文件等），防止 agent 被诱导读取 API key 等机密。

**为什么是 P0：** read_file 是最常用工具，当前无任何路径限制——agent 可以读 `.env`（含 DEEPSEEK_API_KEY）、`.git/config` 等敏感文件。**这是当前最直接的安全缺口。**

## 2. 设计原理（导师讲解：黑名单路径 vs 白名单目录）

### 2.1 两种路径安全策略

| 策略 | 原理 | 特点 |
|------|------|------|
| **敏感路径黑名单** | 拒绝特定文件/目录（.env、.git/） | 简单，但列不全（和 v1 黑名单同样局限） |
| **安全目录白名单** | 只允许读指定目录（如项目目录） | 严格，但限制灵活（读系统文件也被拒） |

**业界做法（Hermes path_security.py）：** 黑名单为主（拦截敏感名）+ 路径规范化（防 `..` 绕过）。本方案采用**敏感名黑名单 + 路径规范化**组合。

### 2.2 关键攻击手法：路径绕过

```
用户: "读一下 ../../etc/passwd"  （相对路径逃逸）
用户: "读一下 ./.env"              （隐藏文件）
用户: "读一下 C:\Users\xie\.ssh\id_rsa"（绝对路径直读密钥）
```

**路径规范化是必须的**：把路径转成绝对路径再判断，否则 `..` 绕过黑名单。

## 3. 设计

### 3.1 新建 `qi_agent/tools/path_security.py`（独立模块）

```python
"""路径安全检查：敏感路径黑名单 + 路径规范化。"""

import os

# 敏感文件名（任意目录下命中即拒绝）
_SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production",
    "id_rsa", "id_ed25519", ".ssh",           # SSH 密钥
    ".git", "config" if False else "",        # 见下
}

# 敏感目录名（路径中任一段命中即拒绝）
_SENSITIVE_DIRS = {".git", ".ssh", "node_modules", "__pycache__", ".venv", "venv"}

# 敏感文件扩展名
_SENSITIVE_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".keystore"}


def is_sensitive_path(path: str) -> bool:
    """判断路径是否敏感（规范化后检查）。

    - 路径转绝对路径（防 .. 绕过）
    - 检查文件名/目录名/扩展名
    - 命中任一规则返回 True
    """
    abs_path = os.path.abspath(path)
    parts = abs_path.replace("\\", "/").split("/")

    filename = parts[-1].lower()
    # 1. 敏感文件名（.env / id_rsa 等）
    if filename in _SENSITIVE_NAMES:
        return True
    # 2. 敏感目录名（.git / .ssh 等）
    if any(part.lower() in _SENSITIVE_DIRS for part in parts):
        return True
    # 3. 敏感扩展名（.pem / .key 等）
    if any(filename.endswith(ext) for ext in _SENSITIVE_EXTENSIONS):
        return True
    return False
```

**设计说明：**
- **路径规范化**（`os.path.abspath`）：`../.env` → `C:\xxx\.env`，黑名单命中
- **三段检查**：文件名（.env）、目录段（.git/）、扩展名（.key）——覆盖主要攻击面
- 大小写不敏感（Windows 路径）

### 3.2 接入 read_file 工具

```python
# read_file.py
from qi_agent.tools.path_security import is_sensitive_path

def read_file(path: str) -> str:
    if is_sensitive_path(path):
        return f"[安全拦截] 路径敏感，禁止读取: {path}"
    ...
```

### 3.3 后续扩展（本方案不做，仅预留）

- `write_file` / `shell` 的路径操作同样调用 `is_sensitive_path`
- 白名单目录模式（未来需要时）

## 4. 测试设计（TDD）

`tests/test_path_security.py`：

| 用例 | 验证点 |
|------|--------|
| `test_sensitive_env_file` | `.env` 被拒 |
| `test_sensitive_relative_dotenv` | `./.env` 被拒 |
| `test_sensitive_parent_escape` | `../.env` 被拒（规范化防绕过） |
| `test_sensitive_ssh_dir` | `.ssh/` 下文件被拒 |
| `test_sensitive_git_dir` | `.git/config` 被拒 |
| `test_sensitive_key_extension` | `server.key` 被拒 |
| `test_normal_file_allowed` | 普通文件（README.md）放行 |
| `test_normal_path_with_sensitive_subdir` | `docs/.git/../normal.md` 规范化后放行？——需明确语义 |
| `test_read_file_blocks_env` | read_file(".env") 返回安全拦截（集成） |
| `test_read_file_allows_normal` | read_file("README.md") 正常读取（集成） |

## 5. 文件变更清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `qi_agent/tools/path_security.py` | 新建 | is_sensitive_path（~40 行） |
| `qi_agent/tools/read_file.py` | 修改 | +路径安全检查（~3 行） |
| `tests/test_path_security.py` | 新建 | 10 个测试 |

## 6. 实施步骤

1. TDD：写 test_path_security.py → RED → 实现 path_security.py → GREEN
2. 接入 read_file.py
3. 全量验证：pytest + ruff
4. 手工验收：`read_file(".env")` 被拦、`read_file("README.md")` 正常
5. commit → tag v0.4.3
6. 归档：devlog + TODO 打勾 ✅

## 7. 验证标准（验收）

- [ ] `uv run pytest` 全绿（新增 ≥10 个测试）
- [ ] `uv run ruff check .` 无错误
- [ ] 手工验证：读 .env 被拦、读正常文件 OK
- [ ] git tag v0.4.3 已打

## 8. 风险与说明

- **黑名单局限（诚实说明）**：敏感名单列不全——但路径安全是"第一道闸"，配合审批机制（TODO）兜底，不追求完美
- **误伤风险**：用户可能合法需要读 .git 下文件（如 .gitignore）——目前 .git 整个目录被拒，如需放宽可只拒 .git/config、.git/credentials 等敏感子文件（后续按需）
- **Windows 路径**：`C:\...` 大小写不敏感处理（.lower()）

## 9. 请评审确认的决策点

1. **敏感名 + 目录 + 扩展名三段检查** 是否认可？
2. **路径规范化（abspath 防 .. 绕过）** 是否认可？
3. **.git 整目录拒绝**（而非只拒敏感子文件）是否认可？
4. **只接入 read_file**（write_file/shell 后续）是否认可？
5. **tag v0.4.3** 是否认可？

---

*评审通过后按本文档第 6 节实施。*
