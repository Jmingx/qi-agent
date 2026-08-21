"""内置工具包：import 即注册（各工具文件在导入时执行 register）。

架构（v0.4.0 升级）：1 工具 1 文件 + register() 显式注册。
新工具 = 在 tools/ 下新建文件并在此导入。
"""

from qi_agent.tools import get_time, read_file, run_python, shell, write_file  # noqa: F401  导入即注册
from qi_agent.tools.registry import (  # noqa: F401  对外导出注册表操作
    _TOOL_REGISTRY,
    execute_tool,
    get_tool_schemas,
    get_tools_by_toolset,
    register,
    tool,
)

__all__ = [
    "register",
    "tool",
    "execute_tool",
    "get_tool_schemas",
    "get_tools_by_toolset",
    "_TOOL_REGISTRY",
    "get_time",
    "read_file",
    "run_python",
    "shell",
    "write_file",
]
