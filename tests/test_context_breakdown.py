"""上下文构成分解测试（阶段 A3）：system/tools/history 分段占比。

设计（方案 2026-08-22-上下文管理）：--debug 展示"当前上下文占用 X%"
（system / 工具 schema / 对话历史 各自 token 占比）——压缩触发前
用户可见。窗口默认 128K（DeepSeek 上限，可配置）。
"""

from qi_agent.context.breakdown import compute_breakdown

_SYSTEM = "你是 qi-agent 助手，请帮助用户。"
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取当前时间",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
_MESSAGES = [
    {"role": "system", "content": _SYSTEM},
    {"role": "user", "content": "现在几点了"},
    {"role": "assistant", "content": "我查一下"},
]


def test_breakdown_structure() -> None:
    """结构完整：total + 分段 + 占比字段。"""
    b = compute_breakdown(_MESSAGES, _TOOLS)
    assert b["total"] > 0
    assert b["system"] > 0
    assert b["tools"] > 0
    assert b["history"] > 0
    # 分段之和 ≈ 总量（system + tools + history）
    assert abs(b["system"] + b["tools"] + b["history"] - b["total"]) <= 1
    # 占比是 0-100 浮点
    assert 0 <= b["history_pct"] <= 100
    assert 0 <= b["window_pct"] <= 100


def test_breakdown_system_dominant() -> None:
    """system 占比计算：短历史下 system 占比高。"""
    b = compute_breakdown(_MESSAGES, _TOOLS)
    assert b["system"] > 0
    assert b["window_pct"] < 100  # 短对话远未占满窗口


def test_breakdown_empty_history() -> None:
    """仅 system 消息（初始状态）→ history 为 0 不崩。"""
    b = compute_breakdown([{"role": "system", "content": _SYSTEM}], [])
    assert b["history"] == 0
    assert b["tools"] == 0
    assert b["total"] == b["system"]


def test_breakdown_custom_window() -> None:
    """窗口可配置（默认 128K）。"""
    b_small = compute_breakdown(_MESSAGES, _TOOLS, window=1000)
    b_big = compute_breakdown(_MESSAGES, _TOOLS, window=128000)
    assert b_small["window_pct"] > b_big["window_pct"]
