"""read_file 工具：读取文本文件内容（1 工具 1 文件示例）。

安全设计：接入 path_security 路径检查——拒绝读取敏感文件
（.env、.ssh、.git 等），防止 agent 被诱导读取 API key 等机密。
"""

from qi_agent.tools.path_security import is_sensitive_path
from qi_agent.tools.registry import register


def read_file(path: str) -> str:
    """读取文本文件并返回内容；文件不存在时返回错误提示。"""
    # 路径安全检查（安全底线，硬编码不可配置）
    if is_sensitive_path(path):
        return f"[安全拦截] 路径敏感，禁止读取: {path}"
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


register(
    name="read_file",
    toolset="builtin",
    handler=read_file,
    description="读取指定路径的文本文件内容",
)
