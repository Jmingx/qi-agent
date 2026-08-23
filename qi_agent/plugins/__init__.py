"""qi-agent 插件系统：机制层（registry/config）+ 内置插件层（builtin/）。

分层（方案 2026-08-23-工具与插件系统分层方案）：
- 机制层（本目录）：插件系统怎么运作——注册表（registry）/ 配置加载（config），
  几乎不再变
- 插件层（builtin/）：系统有哪些能力——1 插件 1 文件，新增能力只动那里

统一导入即注册（对齐 tools/__init__.py 哲学）：
import qi_agent.plugins 即触发所有插件模块的自注册（register_plugin），
cli.py 只需 import load_plugins 即可完成装配，加插件不再改 cli.py。
"""

from qi_agent.plugins import builtin  # noqa: F401  导入即注册
from qi_agent.plugins.registry import (  # noqa: F401  对外导出注册表操作
    get_plugin_names,
    load_plugins,
    register_plugin,
)
