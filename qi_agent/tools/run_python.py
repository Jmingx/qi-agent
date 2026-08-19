"""run_python 工具：软沙箱执行 Python 代码（v1 手写白名单）。

安全设计（四锁，回顾 principles/05）：
- 权限锁：静态扫描黑名单（禁 import os/sys/subprocess、eval/exec、open、反射链）
- 隔离锁：子进程执行，崩溃不影响主 agent
- 时间锁：10 秒硬超时，防死循环
- 安全锁：环境变量白名单，防偷 API key

诚实局限（v1）：黑名单可被字符串拼接（"impo"+"rt os"）或 pathlib 绕过，
v2（RestrictedPython AST 重写）解决此问题。
"""

import os
import subprocess
import sys

from qi_agent.tools.registry import register

# 权限锁：静态扫描的黑名单模式（v1 局限：特征匹配，可被拼接绕过）
_FORBIDDEN_PATTERNS = (
    "import os", "import sys", "import subprocess", "import shutil",
    "import socket", "__import__", "eval(", "exec(", "open(",
    "().__class__", "__subclasses__", "__globals__",
)

# 安全锁：环境变量白名单（只保留 OS 运行必需，丢弃 API key 等敏感变量）
_SAFE_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
)

_MAX_OUTPUT_CHARS = 2000
_TIMEOUT_SECONDS = 10


def _build_safe_env() -> dict:
    """构建白名单环境变量：只保留 _SAFE_ENV_KEYS 中的变量。"""
    return {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}


def _check_code(code: str) -> str | None:
    """静态白名单扫描。返回 None=通过，否则返回拦截原因。"""
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in code:
            return f"代码包含受限操作: {pattern}"
    return None


def run_python(code: str) -> str:
    """在软沙箱中执行 Python 代码并返回输出。

    Args:
        code: 要执行的 Python 代码片段

    Returns:
        执行输出（stdout/stderr），或安全拦截/错误提示。
    """
    # 1. 权限锁：静态扫描
    blocked = _check_code(code)
    if blocked:
        return f"[安全拦截] {blocked}"

    # 2+3+4. 子进程执行 + 时间锁 + 安全锁
    try:
        # -X utf8：强制子进程 UTF-8 模式（PEP 540），输出统一 UTF-8——
        # 否则子进程按系统 locale（Windows=GBK）编码 stdout，emoji 等会编码失败
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", code],
            capture_output=True,
            text=True,
            # 与 shell 同款编码处理：明确 UTF-8 解码 + 容错替换
            encoding="utf-8",
            errors="replace",
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
