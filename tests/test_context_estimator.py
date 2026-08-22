"""token 估算器测试（阶段 A1）：char/4 启发式 + 工具 schema 估算。

设计（方案 2026-08-22-上下文管理）：估算 = 裁剪/压缩的公共地基。
char/4 启发式（Hermes _chars_to_tokens 同款）——精度足够阈值判断，
零依赖；tiktoken 精确计数留可选升级。
"""

from qi_agent.context.estimator import (
    estimate_token_usage,
    estimate_tool_schemas_tokens,
    estimate_tokens,
)


def test_estimate_tokens_basic() -> None:
    """基本估算：char/4（英文为主）。"""
    assert estimate_tokens("hello world") == 2  # 11 char // 4 = 2
    assert estimate_tokens("") == 1  # 空串保底 1（防除零/零值）
    assert estimate_tokens("a") == 1


def test_estimate_tokens_floor() -> None:
    """短文本不估成 0（下限保护）。"""
    assert estimate_tokens("ab") == 1  # 2 // 4 = 0 → max(1, 0)


def test_estimate_tokens_chinese() -> None:
    """中文 char/4 偏高（已知局限）——断言近似值而非精确。"""
    text = "你好世界" * 100  # 400 字
    assert estimate_tokens(text) == 100  # 400 // 4


def test_estimate_tool_schemas_tokens() -> None:
    """工具 schema 估算：JSON 序列化后 char/4。"""
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "获取当前时间",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    tokens = estimate_tool_schemas_tokens(schemas)
    assert tokens >= 1  # 非零
    assert isinstance(tokens, int)


def test_estimate_tool_schemas_empty() -> None:
    """空 schema 列表 → 0。"""
    assert estimate_tool_schemas_tokens([]) == 0


def test_estimate_token_usage_structure() -> None:
    """usage 记录结构：prompt/completion/total 各自累计。"""
    usage = estimate_token_usage(
        [{"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}]
    )
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 20
    assert usage["total_tokens"] == 120
