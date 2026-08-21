"""qi-agent 插件包：监听 agent/* 事件的扩展模块。

统一导入即注册（对齐 tools/__init__.py 哲学）：
import qi_agent.plugins 即触发所有插件模块的自注册（register_plugin），
cli.py 只需 import load_plugins 即可完成装配，加插件不再改 cli.py。
"""

from qi_agent.plugins import security_guard  # noqa: F401  # 导入即注册
from qi_agent.plugins import tool_stats  # noqa: F401  # 导入即注册
from qi_agent.plugins import env_info  # noqa: F401  # 导入即注册
from qi_agent.plugins import approval_gate  # noqa: F401  # 导入即注册
from qi_agent.plugins import resource_monitor  # noqa: F401  # 导入即注册
from qi_agent.plugins.registry import get_plugin_names, load_plugins, register_plugin  # noqa: F401
