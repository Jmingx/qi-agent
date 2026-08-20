"""shell 工具：执行只读命令（1 工具 1 文件示例）。

安全设计：只允许白名单内的只读命令，拒绝危险命令（rm/del/format
等）以及管道/重定向/复合命令，防止 agent 被诱导执行破坏性操作。
"""

from qi_agent.tools.registry import register

# shell 只读白名单：允许的命令前缀（阶段 2 安全设计，完整权限模型留后续阶段）
_READONLY_PREFIXES = (
    "pwd", "ls", "dir", "echo", "cat", "type", "whoami",
    "date", "time", "where", "which", "findstr",
)

# 危险命令关键词：命中即拒绝
_DANGEROUS_KEYWORDS = (
    "rm ", "rm -", "del ", "rd ", "format", "shutdown", "reboot",
    "mkfs", "dd ", ">", ">>", "|", "&&", ";", "curl", "wget",
    "python", "pip", "npm", "git push", "git reset --hard",
)


def shell(command: str, approved: bool = False) -> str:
    """执行只读 shell 命令并返回输出。

    安全设计（v0.4.18 三档权限）：
    - 只允许白名单内的只读命令，拒绝危险命令（rm/del/format 等）以及
      管道/重定向/复合命令，防止 agent 被诱导执行破坏性操作。
    - approved=True：审批同意路径（agent 内部注入，模型 schema 不可见）——
      跳过白名单/危险关键词检查，命令已由用户审批确认。
      模型无法传 approved（参数校验拒绝多余参数）——工具层兜底保持。
    """
    cmd = command.strip().lower()

    if not approved:
        # 1. 危险关键词拦截（必须先于白名单判断）
        for keyword in _DANGEROUS_KEYWORDS:
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

    try:
        result = subprocess.run(
            command,  # 原始命令交给系统执行（只读白名单已拦截风险）
            shell=True,
            capture_output=True,
            text=True,
            # Windows 编码坑：默认按系统 locale（GBK）解码子进程输出，
            # UTF-8/二进制内容会 UnicodeDecodeError（reader 线程炸→输出丢失）。
            # 明确 UTF-8 + errors="replace"：不炸，乱码字节替换为 �
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        output = result.stdout or result.stderr or "(无输出)"
        return output if len(output) <= 2000 else output[:2000] + "\n...[内容过长已截断]"
    except subprocess.TimeoutExpired:
        return "[错误] 命令执行超时（10秒）"
    except OSError as exc:
        return f"[错误] 命令执行失败: {exc}"


register(
    name="shell",
    toolset="builtin",
    handler=shell,
    description=(
        "在 shell 中执行命令：只读命令（pwd/ls/dir/echo/cat/type 等）自动执行；"
        "危险命令（rm/del/shutdown/git push 等）会弹出审批请求，用户同意后执行"
    ),
    # 手写 schema：只暴露 command——approved 是内部参数（agent 审批注入），
    # 不进 schema → 模型看不到也传不了（传了会被参数校验拒为多余参数）
    schema={
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "在 shell 中执行命令：只读命令自动执行；危险命令（rm/del/"
                "shutdown/git push 等）会弹出审批请求，用户同意后执行。"
                "若命令被拒绝（[审批拒绝]），说明用户不同意，不要反复尝试"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令",
                    },
                },
                "required": ["command"],
            },
        },
    },
)
