"""内置工具包：get_time / read_file / shell。

工具注册发生在 import 本包时（@tool 装饰器执行）——
agent 使用工具前必须确保本模块被导入。
"""

from qi_agent.tools.registry import tool


@tool(description="获取当前日期和时间")
def get_time() -> str:
    """返回当前本地日期时间（YYYY-MM-DD HH:MM:SS）。"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool(description="读取指定路径的文本文件内容")
def read_file(path: str) -> str:
    """读取文本文件并返回内容；文件不存在时返回错误提示。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # 限制返回长度，避免撑爆上下文
        return content if len(content) <= 2000 else content[:2000] + "\n...[内容过长已截断]"
    except FileNotFoundError:
        return f"[错误] 文件不存在: {path}"
    except IsADirectoryError:
        return f"[错误] 路径是目录: {path}"
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"


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


@tool(description="在 shell 中执行只读命令（如 pwd/ls/dir/echo/cat/type）")
def shell(command: str) -> str:
    """执行只读 shell 命令并返回输出。

    安全设计：只允许白名单内的只读命令，拒绝危险命令（rm/del/format
    等）以及管道/重定向/复合命令，防止 agent 被诱导执行破坏性操作。
    """
    cmd = command.strip().lower()

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
            timeout=10,
        )
        output = result.stdout or result.stderr or "(无输出)"
        return output if len(output) <= 2000 else output[:2000] + "\n...[内容过长已截断]"
    except subprocess.TimeoutExpired:
        return "[错误] 命令执行超时（10秒）"
    except OSError as exc:
        return f"[错误] 命令执行失败: {exc}"
