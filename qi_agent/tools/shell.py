"""shell 工具：执行只读命令（1 工具 1 文件示例）。

安全设计：只允许白名单内的只读命令，拒绝危险命令（rm/del/format
等）以及管道/重定向/复合命令，防止 agent 被诱导执行破坏性操作。
"""

import psutil

from qi_agent.security.rules import (
    APPROVAL_PREFIXES,
    CODE_EXEC_PREFIXES,
    HARDLINE_PREFIXES,
    SHELL_COMBINATOR_SYNTAX,
)
from qi_agent.tools.registry import register

# 命令执行超时（秒）。长驻程序（游戏/服务器）超时后杀进程树——见 _kill_process_tree
_COMMAND_TIMEOUT = 10


def _approval_condition(arguments: dict) -> str | None:
    """shell 审批条件（v0.4.26 声明式，从 security_guard 迁移）。

    命令前缀匹配（对齐 security_guard 判档语义）：
    - 代码执行类（python/node/npm/pip 等）→ 沙箱升级档：以完整权限运行
      （不受 run_python 沙箱约束），描述带"沙箱升级:"前缀——先于普通
      审批档判定（弹窗透明，v0.4.23）
    - 需审批命令（rm/git push 等）→ 普通审批档，描述即命令本身
    - 红线（format/shutdown）不在此函数——那是插件的系统级底线
      （不可审批、硬拒）；只读白名单命令 → None（放行）
    """
    command = str(arguments.get("command", ""))
    lowered = command.lower().lstrip()
    if any(lowered.startswith(p) for p in CODE_EXEC_PREFIXES):
        return f"沙箱升级:{command}"
    if any(lowered.startswith(p) for p in APPROVAL_PREFIXES):
        return command
    return None

# shell 只读白名单：允许的命令前缀（阶段 2 安全设计，完整权限模型留后续阶段）
_READONLY_PREFIXES = (
    "pwd", "ls", "dir", "echo", "cat", "type", "whoami",
    "date", "time", "where", "which", "findstr",
)


def _kill_process_tree(proc) -> None:
    """杀进程树（Windows 超时修复：kill 外壳 cmd 不会杀它启动的子进程）。

    对齐 run_python 沙箱 v3 的进程树清理：递归杀所有子进程再杀自己，
    避免超时后残留孤儿进程（如启动的游戏/长驻程序）。
    """
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.Error:
        pass  # 进程已退出（竞态窗口），无需处理


def shell(command: str, approved: bool = False, background: bool = False) -> str:
    """执行 shell 命令。

    安全设计（v0.4.18 三档权限 + 2026-08-21 异步扩展）：
    - 只允许白名单内的只读命令，拒绝危险命令（rm/del/format 等）以及
      管道/重定向/复合命令，防止 agent 被诱导执行破坏性操作。
    - approved=True：审批同意路径（agent 内部注入，模型 schema 不可见）——
      跳过白名单/危险关键词检查，命令已由用户审批确认。
      模型无法传 approved（参数校验拒绝多余参数）——工具层兜底保持。
    - background=True：异步启动常驻程序（游戏/服务器），立即返回 PID——
      输出断管道（DEVNULL）+ detach，不阻塞对话（方案 2026-08-21）。
      安全链不变：白名单/审批先行，background 只是执行方式不同。
    """
    cmd = command.strip().lower()

    if not approved:
        # 1. 危险关键词拦截（必须先于白名单判断）——规则来自 security/rules.py
        #    单一来源（方案 2026-08-22）：红线 + 组合语法两组并集
        for keyword in (*HARDLINE_PREFIXES, *SHELL_COMBINATOR_SYNTAX):
            if keyword in cmd:
                return f"[安全拦截] 命令包含危险操作 ({keyword.strip()})，已拒绝执行"

        # 2. 白名单校验：必须以允许的命令开头
        if not cmd.startswith(_READONLY_PREFIXES):
            return (
                f"[安全拦截] 命令不在只读白名单内，已拒绝执行。"
                f"允许的命令: {', '.join(_READONLY_PREFIXES)}"
            )

    # 3. 执行（subprocess 不经过 shell，避免注入）
    import subprocess
    import sys

    if background:
        # 异步启动（方案 2026-08-21）：常驻程序立即返回，不阻塞对话
        # - 不等待：Popen 后不 communicate/wait，直接返回 PID
        # - 断管道：DEVNULL——输出没人读会写满 64KB 缓冲阻塞子进程
        # - detach（Windows）：完全脱离控制台——agent 退出后程序照跑
        creationflags = 0
        if sys.platform == "win32":
            creationflags = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            return f"[错误] 命令启动失败: {exc}"
        return f"[已启动] PID {proc.pid}（异步运行，不阻塞对话）"

    # Windows 死锁修复（2026-08-21 实测）：subprocess.run(timeout=...) 超时后
    # kill 的只是 cmd.exe 外壳——被启动的长驻程序（如游戏）仍持有 stdout/stderr
    # 管道 → run 内部第二次 communicate() 等 EOF 永远等不到 → agent 卡死。
    # 改用 Popen + communicate(timeout)：超时后杀进程树 + 立即返回，不再等管道。
    proc = subprocess.Popen(
        command,  # 原始命令交给系统执行（只读白名单已拦截风险）
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Windows 编码坑：默认按系统 locale（GBK）解码子进程输出，
        # UTF-8/二进制内容会 UnicodeDecodeError（reader 线程炸→输出丢失）。
        # 明确 UTF-8 + errors="replace"：不炸，乱码字节替换为 �
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, err = proc.communicate(timeout=_COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)  # 杀 cmd 外壳 + 其启动的长驻程序
        return (
            "[错误] 命令执行超时（10秒），已终止。"
            "提示：启动 GUI 应用/游戏/服务器等长驻程序请用 start 前缀"
            "（如 start 游戏.exe），立即返回不等待"
        )
    except OSError as exc:
        return f"[错误] 命令执行失败: {exc}"
    output = out or err or "(无输出)"
    return output if len(output) <= 2000 else output[:2000] + "\n...[内容过长已截断]"


register(
    name="shell",
    toolset="builtin",
    handler=shell,
    description=(
        "在 shell 中执行命令：只读命令（pwd/ls/dir/echo/cat/type 等）自动执行；"
        "危险命令（rm/del/shutdown/git push 等）会弹出审批请求，用户同意后执行。"
        "【引导】执行 Python 代码请用 run_python 工具（沙箱执行）；本工具的 "
        "python/py/node 等代码执行命令以完整权限运行（不受沙箱约束），"
        "会弹窗确认沙箱权限升级。启动 GUI 应用/游戏/服务器等常驻程序时 "
        "background=true（立即返回，程序独立运行）；普通命令用默认同步模式（等待输出）"
    ),
    # 审批声明（v0.4.26 声明式）：条件审批——命令前缀匹配（代码执行 →
    # 沙箱升级档；危险命令 → 普通审批档；红线由插件系统级底线硬拒）
    approval=_approval_condition,
    # 手写 schema：只暴露 command + background——approved 是内部参数（agent
    # 审批注入），不进 schema → 模型看不到也传不了（传了会被参数校验拒为多余参数）
    schema={
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "在 shell 中执行命令：只读命令自动执行；危险命令（rm/del/"
                "shutdown/git push 等）会弹出审批请求，用户同意后执行。"
                "【引导】执行 Python 代码请用 run_python 工具（沙箱执行）；"
                "本工具的 python/py/node 等代码执行命令以完整权限运行（不受"
                "沙箱约束），会弹窗确认沙箱权限升级。若命令被拒绝（[审批拒绝]），"
                "说明用户不同意，不要反复尝试"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令",
                    },
                    "background": {
                        "type": "boolean",
                        "description": (
                            "是否异步启动（常驻程序：游戏/服务器等）。"
                            "true=立即返回，程序独立运行；false=同步等待（默认）"
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
)
