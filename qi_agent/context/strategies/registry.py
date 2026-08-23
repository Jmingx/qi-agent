"""策略注册表（方案 2026-08-23）：register_strategy 装饰器 + 按名装配。

独立模块（避免循环导入）：策略文件直接
`from qi_agent.context.strategies.registry import register_strategy`，
不经过包级 __init__（__init__ 只负责导入策略触发注册）。
"""

from qi_agent.context.strategies.base import ContextStrategy

# 注册表：策略名 -> 策略类
_STRATEGY_REGISTRY: dict[str, type[ContextStrategy]] = {}


def register_strategy(cls: type[ContextStrategy]) -> type[ContextStrategy]:
    """策略自注册装饰器（策略文件末尾调用）。"""
    if not cls.name:
        raise ValueError(f"策略 {cls.__name__} 未定义 name")
    if cls.name in _STRATEGY_REGISTRY:
        raise ValueError(f"策略 '{cls.name}' 已存在（重复注册）")
    _STRATEGY_REGISTRY[cls.name] = cls
    return cls


def get_strategy_names() -> list[str]:
    """全部已注册策略名（调试/展示）。"""
    return list(_STRATEGY_REGISTRY.keys())


def build_chain(chain: list[str] | None = None,
                config: dict | None = None) -> list[ContextStrategy]:
    """按配置构建策略链（config.chain 顺序；每策略收各自配置段）。

    Args:
        chain: 策略名列表（None → 默认链）
        config: 完整插件配置（策略名 -> 该策略的配置段）

    Returns:
        按顺序实例化的策略列表
    """
    config = config or {}
    chain = chain or config.get("chain") or ["sticky", "compress", "window"]
    strategies: list[ContextStrategy] = []
    for name in chain:
        cls = _STRATEGY_REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"策略 '{name}' 未注册（可用: {get_strategy_names()}）")
        strategies.append(cls(config.get(name, {})))
    return strategies
