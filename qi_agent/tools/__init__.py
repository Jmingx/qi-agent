"""工具系统：机制层（registry/executor/decision）+ 内置工具层（builtin/）。

分层（方案 2026-08-23-工具与插件系统分层方案）：
- 机制层（本目录）：工具系统怎么运作——注册（registry）/ 执行闭环（executor）/
  决策码（decision），几乎不再变
- 工具层（builtin/）：系统有哪些能力——1 工具 1 文件，新增能力只动那里

注册触发：import qi_agent.tools 即触发 builtin 全部工具自注册（register）。
"""

from qi_agent.tools import builtin  # noqa: F401  导入即注册
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
]
