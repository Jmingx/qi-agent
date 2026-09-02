"""内置插件层：1 插件 1 文件，import 即注册（各插件 register_plugin）。

分层（方案 2026-08-23-工具与插件系统分层方案）：
- 机制层（plugins/ 根）：插件系统怎么运作——registry/config
- 插件层（本目录）：系统有哪些能力——新增插件 = 本目录新建文件 + 在此导入

注册触发链路：plugins/__init__ → from . import builtin → 本文件 import 各插件
→ 各插件模块 register_plugin() 自注册。漏一个 import = 该插件静默不注册。
"""

from qi_agent.plugins.builtin import (  # noqa: F401  导入即注册
    approval_gate,
    context_manager,
    debug_logger,
    env_info,
    memory,
    telemetry_otel,
    resource_monitor,
    security_guard,
    tool_stats,
)
