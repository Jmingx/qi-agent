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
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

from qi_agent.tools.registry import register

# 受限执行器路径（子进程入口，不注册为工具）
_SANDBOX_RUNNER_PATH = str(Path(__file__).parent / "_sandbox_runner.py")

# 权限锁：静态扫描的黑名单模式（v1 快速预检；v2 才是主防线）
_FORBIDDEN_PATTERNS = (
    "import os", "import sys", "import subprocess", "import shutil",
    "import socket", "__import__", "eval(", "exec(", "open(",
    "().__class__", "__subclasses__", "__globals__",
)

# 受限环境可 import 的模块（沙箱内容策略，v0.4.26 从 security_guard 迁移）——
# 审批条件判据：import 白名单外模块 → 降级审批弹窗（用户批准后完整 Python 执行）
_RESTRICTED_MODULES = (
    "math", "random", "json", "statistics", "fractions", "decimal",
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

# 内存锁配置（v0.4.17，双阈值 + 宽限——"允许短暂冲高防误杀"的实现）：
_MEMORY_SOFT_MB = 192    # 软阈值：持续超限开始计时（允许短暂冲高）
_MEMORY_HARD_MB = 256    # 硬阈值：立即 kill（失控底线，不可商量）
_MEMORY_GRACE_SEC = 2.0  # 软阈值宽限：持续超 192MB 达 2s 才 kill
_POLL_INTERVAL = 0.5     # 轮询间隔（秒）


def _tree_rss_mb(pid: int) -> float:
    """进程树 RSS 总和（MB）。

    关键：Windows venv 的 python.exe 是【重定向启动器】——proc.pid 是
    launcher（~4MB），真实解释器是它的子进程（实测 append 炸弹 469MB）。
    只查主进程会漏检内存炸弹——必须递归求和。
    """
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return 0.0
    total = p.memory_info().rss
    for child in p.children(recursive=True):
        total += child.memory_info().rss
    return total / 1024 / 1024


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """连子进程树一起杀（Windows 防残留）。

    进程树：legacy 模式完整 Python 可能再 spawn 子进程——recursive 全杀，
    否则 kill 父进程后子进程残留。
    """
    children: list[psutil.Process] = []
    try:
        p = psutil.Process(proc.pid)
        children = p.children(recursive=True)
        for child in children:
            child.kill()
        p.kill()
    except psutil.NoSuchProcess:
        pass  # 进程已退出（无需杀）
    # Windows kill 异步：等进程树退出——否则 TemporaryDirectory 清理时
    # 目录仍被占用 → WinError 32（全量测试实测失败）
    for child in children:
        try:
            child.wait(timeout=5)
        except psutil.Error:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass  # 极端情况：放弃等待（残留由 OS 回收）


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


def _run_with_limits(cmd: list[str], input_data: str | None, env: dict,
                     cwd: str) -> str:
    """Popen + 轮询循环执行：时间锁（10s）+ 内存锁（双阈值，v0.4.17）。

    内存锁语义（防误杀）：
    - 超过硬阈值 256MB → 立即 kill（失控：暴涨/泄漏，不可商量）
    - 超过软阈值 192MB 且持续 2s → kill（缓慢增长）
    - 瞬时冲高后回落（<2s）→ 不杀（正常任务峰值）

    Args:
        cmd: 子进程命令
        input_data: stdin 内容（None = 不传）
        env: 沙箱环境（双重过滤后）
        cwd: 工作目录（临时目录）

    Returns:
        执行输出（截断后），或超时/内存超限提示
    """
    proc = subprocess.Popen(
        cmd, env=env, cwd=cwd,
        stdin=subprocess.PIPE,  # 受限模式写用户代码；不传则关闭（EOF）
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    # 写入 stdin（受限模式用户代码）——量小（KB 级），管道缓冲足够，不阻塞
    if input_data is not None:
        proc.stdin.write(input_data)
        proc.stdin.close()
    # drain 线程：持续读 stdout/stderr——防止管道缓冲区满（64KB）导致
    # 子进程阻塞在 write 而父进程轮询等退出（经典 Popen 死锁坑）
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain(stream, chunks: list[str]) -> None:
        for line in stream:
            chunks.append(line)

    t_out = threading.Thread(
        target=_drain, args=(proc.stdout, stdout_chunks), daemon=True
    )
    t_err = threading.Thread(
        target=_drain, args=(proc.stderr, stderr_chunks), daemon=True
    )
    t_out.start()
    t_err.start()
    start = time.monotonic()
    over_soft_since: float | None = None  # 超过软阈值的起始时刻（回落复位）
    while proc.poll() is None:
        # ① 时间锁：超时 → kill 进程树
        if time.monotonic() - start > _TIMEOUT_SECONDS:
            _kill_process_tree(proc)
            return f"[错误] 代码执行超时（{_TIMEOUT_SECONDS}秒）"
        # ② 内存锁：双阈值判定（进程树 RSS 总和——venv launcher 陷阱）
        mem_mb = _tree_rss_mb(proc.pid)
        if mem_mb <= 0.0:
            break  # 进程已退出（NoSuchProcess）
        if mem_mb > _MEMORY_HARD_MB:
            # 硬阈值：失控 → 立即 kill
            _kill_process_tree(proc)
            return (
                f"[安全拦截] 内存超限（>{_MEMORY_HARD_MB}MB），"
                f"已终止执行"
            )
        if mem_mb > _MEMORY_SOFT_MB:
            # 软阈值：开始/持续计时
            if over_soft_since is None:
                over_soft_since = time.monotonic()
            elif time.monotonic() - over_soft_since > _MEMORY_GRACE_SEC:
                _kill_process_tree(proc)
                return (
                    f"[安全拦截] 内存持续超限（>{_MEMORY_SOFT_MB}MB "
                    f"超过{_MEMORY_GRACE_SEC}s），已终止执行"
                )
        else:
            over_soft_since = None  # 回落：复位计时（瞬时峰值放行）
        time.sleep(_POLL_INTERVAL)
    # 进程结束：等 drain 线程读到 EOF（输出已全部收完）
    t_out.join(timeout=1.0)
    t_err.join(timeout=1.0)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    output = stdout or stderr or "(无输出)"
    if len(output) > _MAX_OUTPUT_CHARS:
        return output[:_MAX_OUTPUT_CHARS] + "\n...[输出已截断]"
    return output


def run_python(code: str, approved: bool = False) -> str:
    """在软沙箱中执行 Python 代码并返回输出。

    沙箱模式（v0.4.23 审批档，环境变量开关已退役）：
    - approved=False（默认）：restricted 受限执行（v2 主防线，最安全）
    - approved=True（用户弹窗审批后 agent 内部注入，模型 schema 不可见）：
      该次调用降级为完整 Python（legacy）——跳过 v1 静态扫描，用户承担该次风险。
      模型无法传 approved（schema 不暴露 + 参数校验拒绝多余参数）——
      降级只发生在用户批准之后（对齐 shell 三档权限）。

    Args:
        code: 要执行的 Python 代码片段
        approved: 审批注入（内部参数，非模型可传）

    Returns:
        执行输出（stdout/stderr），或安全拦截/错误提示。
    """
    # 1. v1 快速预检：静态扫描（明显恶意直接拒，省子进程）
    #    approved=True 时跳过——用户已批准降级，承担该次代码风险
    if not approved:
        blocked = _check_code(code)
        if blocked:
            return f"[安全拦截] {blocked}"

    # 2+3+4+5. 临时工作目录 + 子进程执行 + 时间锁 + 安全锁 + 内存锁
    # restricted（默认）：受限执行器（v2 主防线）；legacy（仅审批降级）：完整 Python
    # 隔离锁（v0.4.16）：cwd=临时目录——碰不到项目文件
    # 内存锁（v0.4.17）：Popen 轮询双阈值（_run_with_limits 内）
    try:
        with tempfile.TemporaryDirectory(prefix="qi_sandbox_") as tmpdir:
            if approved:  # 沙箱降级（v0.4.23）：完整 Python 执行该次代码
                cmd = [sys.executable, "-X", "utf8", "-c", code]
                input_data = None
            else:
                cmd = [sys.executable, "-X", "utf8", _SANDBOX_RUNNER_PATH]
                input_data = code  # 用户代码走 stdin（避免命令行长度/转义问题）
            return _run_with_limits(
                cmd, input_data, env=_build_safe_env(), cwd=tmpdir
            )
    except OSError as exc:
        return f"[错误] 执行失败: {exc}"


def _approval_condition(arguments: dict) -> str | None:
    """run_python 审批条件（v0.4.26 声明式，从 security_guard 迁移）。

    import 受限白名单外模块 → 返回降级审批描述；否则 None（放行）。
    工具自声明权限策略——security_guard 插件查 registry 执行。
    """
    code = str(arguments.get("code", ""))
    for m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M):
        module = m.group(1).split(".")[0]
        if module not in _RESTRICTED_MODULES:
            return f"代码需要 import '{module}'（受限环境白名单外），需降级审批"
    return None


register(
    name="run_python",
    toolset="builtin",
    handler=run_python,
    description=(
        "在软沙箱中执行 Python 代码片段（安全受限：禁危险操作，10秒超时）。"
        "【引导】模型生成的探测/处理/计算类 Python 代码请用本工具（沙箱执行）；"
        "只执行单条 shell 命令用 shell 工具。受限环境只支持 math/random/json "
        "等基础模块；需要其他模块（如 requests）的代码会弹窗请求降级审批，"
        "用户同意后以完整 Python 执行"
    ),
    # 审批声明（v0.4.26 声明式）：条件审批——import 白名单外 → 降级弹窗
    approval=_approval_condition,
    # 手写 schema：只暴露 code——approved 是内部参数（agent 审批注入），
    # 不进 schema → 模型看不到也传不了（传了会被参数校验拒为多余参数）
    # 注意：模型看到的描述是 schema.function.description（register description
    # 仅内部元数据）——引导文案必须写在这里
    schema={
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "在软沙箱中执行 Python 代码片段（安全受限：禁危险操作，10秒超时）。"
                "【引导】模型生成的探测/处理/计算类 Python 代码请用本工具（沙箱执行）；"
                "只执行单条 shell 命令用 shell 工具。受限环境只支持 math/random/json "
                "等基础模块；需要其他模块（如 requests）的代码会弹窗请求降级审批，"
                "用户同意后以完整 Python 执行"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    },
                },
                "required": ["code"],
            },
        },
    },
)
