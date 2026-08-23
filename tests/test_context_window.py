"""滑动窗口裁剪测试（阶段 B1 + L1 协议正确性）。

L1 死线（方案 2026-08-22 测评保障）：
  ① tool_calls 消息与其 tool 结果成对（无孤儿）
  ② role 严格交替（无连续同 role）
  ③ system（含 sticky）永不裁
  ④ 裁剪后 anchor 注入（user 角色，模型知道历史被裁）
"""

from qi_agent.context.window import ANCHOR_TEXT, _group_messages, trim_messages


def _msg(role: str, content: str = "") -> dict:
    return {"role": role, "content": content}


def _tool_calls_msg(content: str, names: list[str]) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": n, "arguments": "{}"}}
            for i, n in enumerate(names)
        ],
    }


def _tool_result(content: str) -> dict:
    return {"role": "tool", "content": content, "tool_call_id": "call_0"}


# ── 消息组切分 ───────────────────────────────────────────────────────────


def test_group_basic() -> None:
    """普通消息各自成组。"""
    groups = _group_messages([_msg("user", "hi"), _msg("assistant", "hello")])
    assert len(groups) == 2
    assert len(groups[0]) == 1 and len(groups[1]) == 1


def test_group_tool_calls_pair() -> None:
    """tool_calls 消息 + 后续 tool 结果 = 一组（成对性）。"""
    messages = [
        _msg("user", "查一下"),
        _tool_calls_msg("", ["get_time"]),
        _tool_result("12:00"),
        _msg("assistant", "现在是 12:00"),
    ]
    groups = _group_messages(messages)
    assert len(groups) == 3  # user / (assistant+tool) / assistant
    assert len(groups[1]) == 2  # 成对
    assert groups[1][0]["tool_calls"] and groups[1][1]["role"] == "tool"


# ── 裁剪行为 ─────────────────────────────────────────────────────────────


def test_under_budget_no_trim() -> None:
    """预算内 → 原样返回（0 组裁剪）。"""
    messages = [_msg("system", "sys"), _msg("user", "hi")]
    result, trimmed = trim_messages(messages, 100_000)
    assert trimmed == 0
    assert result == messages


def test_trim_oldest_first() -> None:
    """超预算 → 从最旧开始裁（保最近）。"""
    # 每条 user 消息约 50 字 → ~12 token；5 条 ≈ 60+ token
    messages = [_msg("system", "sys")] + [
        _msg("user", "这是一条比较长的历史消息内容" * 3) for _ in range(5)
    ]
    result, trimmed = trim_messages(messages, 30)
    assert trimmed > 0
    assert result[0] == _msg("system", "sys")  # system 保留
    assert "这是" in result[-1]["content"]  # 最新消息保留


def test_system_never_trimmed() -> None:
    """system 永不裁（即使预算极小，system 也保留）。"""
    messages = [_msg("system", "sys" * 100)] + [_msg("user", "hi")]
    result, _ = trim_messages(messages, 1)
    assert result[0]["role"] == "system"


# ── L1 协议正确性 ────────────────────────────────────────────────────────


def test_l1_tool_calls_pair_preserved() -> None:
    """L1①：裁剪后 tool_calls 与其 tool 结果不成孤儿。"""
    messages = [
        _msg("system", "sys"),
        _msg("user", "早期问题"),
        _tool_calls_msg("", ["get_time"]),
        _tool_result("10:00"),
        _msg("assistant", "早期回答"),
        _msg("user", "最新问题" * 50),  # 长 → 迫使裁掉早期
    ]
    result, trimmed = trim_messages(messages, 15)
    assert trimmed > 0
    # 遍历：任何 tool 结果前面必须是同一个组（assistant tool_calls）
    in_tool_sequence = False
    for i, msg in enumerate(result):
        if msg.get("role") == "tool":
            assert in_tool_sequence, "tool 结果出现在组外（孤儿）"
            # 组内 tool 结果后不能直接跟另一个 tool 结果以外的断点
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            in_tool_sequence = True
        elif msg.get("role") != "tool":
            in_tool_sequence = False


def test_l1_role_alternation() -> None:
    """L1②：裁剪后无连续同 role（user 或 assistant 连续 = 协议拒绝）。"""
    messages = [
        _msg("system", "sys"),
        _msg("user", "a" * 200), _msg("assistant", "b" * 200),
        _msg("user", "c" * 200), _msg("assistant", "d" * 200),
        _msg("user", "e" * 200), _msg("assistant", "f" * 200),
        _msg("user", "最新" * 100),
    ]
    result, trimmed = trim_messages(messages, 50)
    assert trimmed > 0
    roles = [m["role"] for m in result if m["role"] != "system"]
    for i in range(1, len(roles)):
        assert roles[i] != roles[i - 1], f"连续同 role: {roles}"


def test_l1_anchor_injected_when_starts_with_assistant() -> None:
    """L1③④：裁剪后首条非 system 是 assistant → 注入 user anchor。"""
    messages = [
        _msg("system", "sys"),
        _msg("user", "a" * 300),
        _msg("assistant", "b" * 300),
        _msg("user", "c" * 300),
        _msg("assistant", "d" * 300),  # 最新是 assistant
    ]
    result, trimmed = trim_messages(messages, 30)
    assert trimmed > 0
    non_system = [m for m in result if m["role"] != "system"]
    assert non_system[0]["role"] == "user"  # 交替满足
    assert non_system[0]["content"] == ANCHOR_TEXT  # anchor 注入
