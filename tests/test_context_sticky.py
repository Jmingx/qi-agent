"""sticky notes 纯函数测试（阶段 B3）。

注意：agent 集成（挂载/裁剪免疫）已迁移到 test_plugin_context_manager.py
（2026-08-22 用户架构修正：上下文管理插件化，agent 零侵入）——本文件
只测 sticky 存储与渲染本身。
"""

import pytest

from qi_agent.context.sticky import get_sticky_text, list_sticky, remember, reset


@pytest.fixture(autouse=True)
def _clean_sticky():
    yield
    reset()


def test_remember_and_render() -> None:
    """remember → get_sticky_text 渲染（去重）。"""
    remember("我叫小明")
    remember("我叫小明")  # 去重
    remember("我喜欢吃苹果")
    text = get_sticky_text()
    assert "小明" in text
    assert text.count("小明") == 1  # 去重生效
    assert "苹果" in text


def test_remember_empty_ignored() -> None:
    """空内容/纯空白 → 忽略。"""
    remember("")
    remember("   ")
    assert get_sticky_text() == ""


def test_list_sticky() -> None:
    """查看当前 sticky 列表。"""
    remember("A")
    remember("B")
    assert list_sticky() == ["A", "B"]


def test_reset() -> None:
    """reset 清空（测试隔离/会话重置）。"""
    remember("A")
    reset()
    assert get_sticky_text() == ""
