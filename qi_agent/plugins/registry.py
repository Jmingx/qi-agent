"""插件注册表：插件自注册 + 按配置统一装配。

设计（方案 docs/plans/2026-08-19-插件装配升级方案.md）：
- register_plugin 自注册：插件文件末尾调用，登记"存在"（对齐 tools/registry.py）
- load_plugins 统一装配：按配置决定"启用"，实例化 + install(bus)
- 配置优先级：配置文件显式声明 > 插件 default_enabled > 零配置全默认

职责划分：注册表管"有哪些插件"，配置文件管"启用哪些"——加插件不再改 cli.py。
"""

from dataclasses import dataclass
from typing import Callable

from qi_agent.events import EventBus

# 注册表：插件名 -> 插件条目（对齐 _TOOL_REGISTRY 模式）
_PLUGIN_REGISTRY: dict[str, "PluginEntry"] = {}


@dataclass
class PluginEntry:
    """注册表中的一个插件条目。"""

    name: str                    # 插件名（唯一）
    factory: Callable            # 无参工厂：构造插件实例
    description: str = ""        # 一句话描述
    default_enabled: bool = True # 默认开关（配置文件未覆盖时使用）


def register_plugin(
    name: str,
    factory: Callable,
    description: str = "",
    default_enabled: bool = True,
) -> None:
    """插件自注册入口（插件文件末尾调用）。

    Args:
        name: 插件名（唯一，重复注册抛错）
        factory: 无参工厂函数/类（实例化插件对象）
        description: 一句话描述
        default_enabled: 默认是否启用（配置文件可覆盖）

    Raises:
        ValueError: 插件名重复
    """
    if name in _PLUGIN_REGISTRY:
        raise ValueError(f"插件 '{name}' 已存在（重复注册）")
    _PLUGIN_REGISTRY[name] = PluginEntry(
        name=name,
        factory=factory,
        description=description,
        default_enabled=default_enabled,
    )


def load_plugins(bus: EventBus, config: dict | None = None) -> list:
    """按配置装配所有启用的插件：实例化 + install(bus)。

    Args:
        bus: 目标事件总线（agent.events）
        config: 插件配置 dict（插件名 -> {"enabled": bool}）；None=全用默认

    Returns:
        已安装的插件实例列表（供汇报/测试用）
    """
    config = config or {}
    installed: list = []
    for name, entry in _PLUGIN_REGISTRY.items():
        # 配置优先级：显式声明 > 插件默认开关（2.2 节规则）
        enabled = config.get(name, {}).get("enabled", entry.default_enabled)
        if not enabled:
            continue
        plugin = entry.factory()
        plugin.install(bus)  # 统一入口约定（v0.4.8 确立）
        installed.append(plugin)
    return installed


def get_plugin_names() -> list[str]:
    """返回全部已注册插件名（调试/日志用）。"""
    return list(_PLUGIN_REGISTRY.keys())
