"""环境信息插件测试：检测 + pre-step 幂等注入。

方案：docs/plans/2026-08-19-安全与环境增强方案.md（决策点 1-5 已批准）
"""

import platform

from qi_agent.plugins.env_info import EnvInfoPlugin


def _make_plugin() -> EnvInfoPlugin:
    return EnvInfoPlugin()


def test_detect_returns_platform() -> None:
    """_detect 应包含运行时平台信息。"""
    info = _make_plugin()._detect()
    assert platform.system() in info
    assert "[环境信息]" in info


def test_inject_first_time() -> None:
    """首次注入：环境消息应插入消息列表最前。"""
    plugin = _make_plugin()
    messages = [
        {"role": "system", "content": "你是一个有用的助手。"},
        {"role": "user", "content": "你好"},
    ]
    result = plugin._inject(messages)
    assert len(result) == 3
    assert result[0]["role"] == "system"
    assert result[0]["content"].startswith("[环境信息]")


def test_inject_idempotent() -> None:
    """第二次注入应原样返回（幂等，pre-step 每 step 都触发）。"""
    plugin = _make_plugin()
    messages = [
        {"role": "system", "content": "你是一个有用的助手。"},
        {"role": "user", "content": "你好"},
    ]
    once = plugin._inject(messages)
    twice = plugin._inject(once)
    assert twice is once  # 已含环境消息 → 原样返回（同一对象）


def test_inject_after_clear() -> None:
    """clear_context 后（消息重置）应能重新注入。"""
    plugin = _make_plugin()
    fresh = [{"role": "system", "content": "你是一个有用的助手。"}]
    result = plugin._inject(fresh)
    assert result[0]["content"].startswith("[环境信息]")
