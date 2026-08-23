"""策略注册表导出（方案 2026-08-23）：导入全部策略触发注册。

注册逻辑在 registry.py（独立模块防循环导入）；本文件只负责
"导入即注册"——导入全部策略模块 + re-export 常用接口。
"""

from qi_agent.context.strategies import (  # noqa: F401  导入即注册
    compress,
    sticky,
    window,
)
from qi_agent.context.strategies.base import ContextInfo, ContextStrategy  # noqa: F401
from qi_agent.context.strategies.registry import (  # noqa: F401
    build_chain,
    get_strategy_names,
    register_strategy,
)
