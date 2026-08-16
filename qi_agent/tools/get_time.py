"""get_time 工具：获取当前时间（1 工具 1 文件示例）。"""

from qi_agent.tools.registry import register


def get_time() -> str:
    """返回当前本地日期时间（YYYY-MM-DD HH:MM:SS）。"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


register(
    name="get_time",
    toolset="builtin",
    handler=get_time,
    description="获取当前日期和时间",
)
