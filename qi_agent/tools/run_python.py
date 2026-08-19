"""run_python 工具：软沙箱执行 Python 代码（v1 手写白名单 + v2 RestrictedPython）。

安全设计（四锁，回顾 principles/05）：
- 权限锁：静态扫描黑名单（禁 import os/sys/subprocess、eval/exec、open、反射链）
- 隔离锁：子进程执行，崩溃不影响主 agent
- 时间锁：10 秒硬超时，防死循环
- 安全锁：环境变量双重过滤，防偷 API key

v2（v0.4.13）：RestrictedPython 受限执行（AST 重写 + 受限内建）兜底 v1
拦不住的绕过（拼接/反射/白名单外模块）。双层防线：
- v1 静态扫描 = 快速预检（明显恶意直接拒，省子进程）
- v2 受限执行 = 主防线（兜底一切，详见 principles/09）

诚实局限（v1）：黑名单可被字符串拼接（"impo"+"rt os"）或 pathlib 绕过——
v2 已解决（受限环境无 import 能力）。
"""

import os
import subprocess
import sys
from pathlib import Path

from qi_agent.tools.registry import register

# 沙箱执行模式（过渡方案，v0.4.13）：
#   restricted（默认）：v2 受限执行（最安全）
#   legacy（显式降级）：v1 静态扫描 + 完整 Python（现状行为，有 v1 绕过风险）
# ⚠️ 过渡方案：降级操作将来应走【用户审核机制】（shell 三档权限 TODO）——
#    环境变量开关届时退役，改为弹窗确认"确认降级沙箱安全等级？"
_SANDBOX_MODE = os.getenv("QI_SANDBOX_MODE", "restricted").strip().lower()
if _SANDBOX_MODE not in ("restricted", "legacy"):
    _SANDBOX_MODE = "restricted"  # 未知值回落最安全默认

# 受限执行器路径（子进程入口，不注册为工具）
_SANDBOX_RUNNER_PATH = str(Path(__file__).parent / "_sandbox_runner.py")

# 权限锁：静态扫描的黑名单模式（v1 快速预检；v2 才是主防线）
_FORBIDDEN_PATTERNS = (
    "import os", "import sys", "import subprocess", "import shutil",
    "import socket", "__import__", "eval(", "exec(", "open(",
    "().__class__", "__subclasses__", "__globals__",
)

# 第一道防线：密钥特征子串（对齐 Hermes _SECRET_SUBSTRINGS，先黑后白）
# "PASS" 故意不加——误伤 BYPASS_CACHE/COMPASS_DIR/PASSENGER_HOST 等合法变量
# （Hermes 踩坑注释原话；PASSWD 是密码缩写，保留拦截）
_SENSITIVE_KEY_SUBSTRINGS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
    "PASSWD", "AUTH", "DSN", "WEBHOOK",
    "CREDS", "BEARER", "APIKEY",
)

# 第二道防线：环境变量白名单（只保留 OS 运行必需，丢弃 API key 等敏感变量）
_SAFE_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
)

# 沙箱自身配置前缀：这些变量需透传给子进程（runner 读取），不是密钥
# （QI_SANDBOX_MODE / QI_SANDBOX_EXTRA_BUILTINS / QI_SANDBOX_EXTRA_MODULES）
_SANDBOX_CONFIG_PREFIX = "QI_SANDBOX_"

_MAX_OUTPUT_CHARS = 2000
_TIMEOUT_SECONDS = 10


def _build_safe_env() -> dict:
    """双重过滤构建沙箱环境（对齐 Hermes _scrub_child_env 第 ② 条，先黑后白）。

    ① 密钥子串拦截：变量名含密钥特征（KEY/TOKEN/SECRET...）→ 丢弃。
       防御纵深：即使白名单将来改宽/改成前缀匹配，密钥也进不来
    ② 白名单保留：OS 运行必需的精确名单（PATH/SYSTEMROOT...）
    ③ 沙箱配置透传：QI_SANDBOX_* 前缀（runner 的配置来源，非密钥）
    """
    safe = {}
    for k, v in os.environ.items():
        upper = k.upper()  # 环境变量名大小写不敏感（Windows 尤其）
        # ① 密钥子串拦截（第一道，先扫——顺序关键）
        if any(s in upper for s in _SENSITIVE_KEY_SUBSTRINGS):
            continue
        # ③ 沙箱配置透传（runner 读取自己的配置）
        if upper.startswith(_SANDBOX_CONFIG_PREFIX):
            safe[k] = v
            continue
        # ② 安全名单保留（第二道）
        if k in _SAFE_ENV_KEYS:
            safe[k] = v
    return safe


def _check_code(code: str) -> str | None:
    """v1 静态扫描（快速预检）。返回 None=通过，否则返回拦截原因。"""
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
    # 1. v1 快速预检：静态扫描（明显恶意直接拒，省子进程）
    blocked = _check_code(code)
    if blocked:
        return f"[安全拦截] {blocked}"

    # 2+3+4. 子进程执行 + 时间锁 + 安全锁
    # restricted：受限执行器（v2 主防线）；legacy：完整 Python（过渡降级）
    if _SANDBOX_MODE == "legacy":
        cmd = [sys.executable, "-X", "utf8", "-c", code]
        input_data = None
    else:
        cmd = [sys.executable, "-X", "utf8", _SANDBOX_RUNNER_PATH]
        input_data = code  # 用户代码走 stdin（避免命令行长度/转义问题）
    try:
        # -X utf8：强制子进程 UTF-8 模式（PEP 540），输出统一 UTF-8——
        # 否则子进程按系统 locale（Windows=GBK）编码 stdout，emoji 等会编码失败
        result = subprocess.run(
            cmd,
            input=input_data,
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
