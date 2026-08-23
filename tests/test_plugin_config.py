"""插件配置加载测试：tomllib 读 plugins.toml（可选文件）。

方案：docs/plans/2026-08-19-插件装配升级方案.md（决策点 2：TOML 零依赖）
"""

import pytest

from qi_agent.plugins.config import load_plugin_config


def test_missing_config_returns_empty(tmp_path) -> None:
    """配置文件不存在应返回空 dict（全用插件默认开关）。"""
    missing = tmp_path / "no_such.toml"
    assert load_plugin_config(missing) == {}


def test_parse_valid_toml(tmp_path) -> None:
    """正常 TOML 应解析出 {插件名: {"enabled": bool}}。"""
    cfg = tmp_path / "plugins.toml"
    cfg.write_text(
        "[tool_stats]\n"
        'enabled = true\n'
        "\n"
        "[security_guard]\n"
        'enabled = false\n',
        encoding="utf-8",
    )
    result = load_plugin_config(cfg)
    assert result == {
        "tool_stats": {"enabled": True},
        "security_guard": {"enabled": False},
    }


def test_invalid_toml_raises(tmp_path) -> None:
    """坏 TOML 应抛明确错误（tomllib.TOMLDecodeError）。"""
    cfg = tmp_path / "plugins.toml"
    cfg.write_text("[tool_stats\nenabled = true\n", encoding="utf-8")  # 缺 ] 
    with pytest.raises(Exception):
        load_plugin_config(cfg)
