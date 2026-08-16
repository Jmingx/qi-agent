"""内置工具包：import 即注册（@tool 装饰器在导入时执行）。

对外导出注册表操作函数，便于 agent 使用。
"""

from qi_agent.tools import builtin  # noqa: F401  确保工具注册发生
from qi_agent.tools.registry import _TOOL_REGISTRY, execute_tool, get_tool_schemas, tool

__all__ = ["tool", "execute_tool", "get_tool_schemas", "_TOOL_REGISTRY", "builtin"]
