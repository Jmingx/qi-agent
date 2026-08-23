"""插件注册表测试：register_plugin 自注册 + load_plugins 按配置装配。

方案：docs/plans/2026-08-19-插件装配升级方案.md（决策点 1-6 已批准）
"""

import pytest

from qi_agent.events import EventBus
from qi_agent.plugins.registry import (
    _PLUGIN_REGISTRY,
    get_plugin_names,
    load_plugins,
    register_plugin,
)


@pytest.fixture(autouse=True)
def isolate_plugins():
    """隔离注册表：测试前后备份/恢复。

    防止 security_guard（默认启用）等真实插件污染 load_plugins 的断言——
    registry 单元测试只关心自己注册的 FakePlugin。
    """
    backup = dict(_PLUGIN_REGISTRY)
    _PLUGIN_REGISTRY.clear()
    yield
    _PLUGIN_REGISTRY.clear()
    _PLUGIN_REGISTRY.update(backup)


class FakePlugin:
    """测试插件：install 记录调用（factory 约定：接收配置段 dict）。"""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        self.installed_on: EventBus | None = None

    def install(self, bus: EventBus) -> None:
        self.installed_on = bus


def _cleanup(name: str) -> None:
    """测试后清理注册表。"""
    _PLUGIN_REGISTRY.pop(name, None)


def test_register_and_get() -> None:
    """注册后 get_plugin_names 应可见。"""
    register_plugin("fake_a", FakePlugin, description="测试插件")
    try:
        assert "fake_a" in get_plugin_names()
    finally:
        _cleanup("fake_a")


def test_duplicate_register_raises() -> None:
    """重复注册同名插件应抛错。"""
    register_plugin("dup_plugin", FakePlugin)
    try:
        with pytest.raises(ValueError, match="已存在"):
            register_plugin("dup_plugin", FakePlugin)
    finally:
        _cleanup("dup_plugin")


def test_load_all_default() -> None:
    """空配置时应按 default_enabled 装配。"""
    register_plugin("default_on", FakePlugin, default_enabled=True)
    register_plugin("default_off", FakePlugin, default_enabled=False)
    try:
        installed = load_plugins(EventBus())
        names = [type(p).__name__ for p in installed]
        assert "FakePlugin" in names  # default_on 被装配
        assert len(installed) == 1    # default_off 不装配
    finally:
        _cleanup("default_on")
        _cleanup("default_off")


def test_load_respects_config() -> None:
    """配置 enabled=false 应覆盖默认开关，不装配。"""
    register_plugin("cfg_off", FakePlugin, default_enabled=True)
    try:
        config = {"cfg_off": {"enabled": False}}
        installed = load_plugins(EventBus(), config)
        assert installed == []
    finally:
        _cleanup("cfg_off")


def test_load_config_overrides_default_both_ways() -> None:
    """配置应能双向覆盖默认开关（开→关、关→开）。"""
    register_plugin("cfg_on", FakePlugin, default_enabled=False)
    register_plugin("cfg_off2", FakePlugin, default_enabled=True)
    try:
        config = {"cfg_on": {"enabled": True}, "cfg_off2": {"enabled": False}}
        installed = load_plugins(EventBus(), config)
        names = [type(p).__name__ for p in installed]
        assert "FakePlugin" in names  # 只有 cfg_on 被装配
        assert len(installed) == 1
    finally:
        _cleanup("cfg_on")
        _cleanup("cfg_off2")


def test_installed_returns_instances() -> None:
    """load_plugins 应返回已 install 的实例（bus 已注入）。"""
    register_plugin("inst_check", FakePlugin)
    try:
        bus = EventBus()
        installed = load_plugins(bus)
        assert len(installed) == 1
        assert installed[0].installed_on is bus
    finally:
        _cleanup("inst_check")


def test_unknown_name_in_config_ignored() -> None:
    """配置里未登记的插件名应被忽略（不报错）。"""
    register_plugin("known_plugin", FakePlugin, default_enabled=False)
    try:
        # 配置里有个不存在的插件名 + 一个已知但默认关闭的
        config = {"ghost_plugin": {"enabled": True}}
        installed = load_plugins(EventBus(), config)
        assert installed == []  # ghost 不装配（未登记），known 默认关不装配
    finally:
        _cleanup("known_plugin")


def test_factory_receives_config() -> None:
    """factory 应收到除 enabled 外的配置段（决策点 1：约定升级）。"""
    register_plugin("cfg_plugin", FakePlugin, default_enabled=True)
    try:
        config = {
            "cfg_plugin": {
                "enabled": True,
                "blacklist": {"shell": ["git push"]},
            }
        }
        installed = load_plugins(EventBus(), config)
        assert len(installed) == 1
        # enabled 被过滤，业务配置完整传入
        assert installed[0].config == {"blacklist": {"shell": ["git push"]}}
    finally:
        _cleanup("cfg_plugin")
