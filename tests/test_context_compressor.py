"""压缩器测试（阶段 C）：真实 usage 触发 + 摘要压缩 + L1 协议。

用户要求（2026-08-22）：token 消耗不用估算——should_compress 用
response 真实 usage（result.usage.prompt_tokens），估算器仅作兜底。

L1 死线（压缩后消息序列发给 API 合法）：
  ① 摘要块必须是 user 角色（role 交替）
  ② 保留的最近消息从组边界开始（不从 tool 结果/孤儿开始）
  ③ system 保留在最前
"""

from qi_agent.context.compressor import (
    assemble,
    build_summary_prompt,
    compress_messages,
    should_compress,
)


# ── should_compress（真实 usage 驱动） ──────────────────────────────────


def test_should_compress_over_threshold() -> None:
    """真实 prompt_tokens 超阈值 → 需要压缩。"""
    # 窗口 128K、阈值 0.7 → 89.6K 触发
    assert should_compress(90_000, 128_000, 0.7) is True
    assert should_compress(128_000, 128_000, 0.7) is True


def test_should_compress_under_threshold() -> None:
    """未超阈值 → 不压缩。"""
    assert should_compress(50_000, 128_000, 0.7) is False
    assert should_compress(89_599, 128_000, 0.7) is False


def test_should_compress_boundary() -> None:
    """边界：恰好等于阈值 → 触发（>= 语义）。"""
    assert should_compress(89_600, 128_000, 0.7) is True


# ── 摘要提示构造 ─────────────────────────────────────────────────────────


def test_build_summary_prompt_contains_sections() -> None:
    """摘要提示含结构化分区（关键事实/用户要求/决策/未完成）。"""
    messages = [
        {"role": "user", "content": "我叫小明，正在开发 qi-agent"},
        {"role": "assistant", "content": "好的，记住你是小明"},
    ]
    prompt = build_summary_prompt(messages)
    assert "关键事实" in prompt
    assert "用户要求" in prompt
    assert "未完成" in prompt
    assert "我叫小明" in prompt  # 内容进提示


# ── 压缩切分与组装（L1 协议） ───────────────────────────────────────────


def test_compress_messages_splits_early_and_recent() -> None:
    """早期历史与最近消息分离（keep_recent 边界）。"""
    messages = [{"role": "user", "content": f"第{i}轮"} for i in range(12)]
    early, recent = compress_messages(messages, keep_recent=4)
    assert len(recent) == 4
    assert len(early) == 8
    assert recent[0]["content"] == "第8轮"  # 最近 4 条
    assert early[-1]["content"] == "第7轮"  # 早期最后一条


def test_assemble_l1_protocol() -> None:
    """组装后协议合法：system 最前 + 摘要 user 块 + 最近消息。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "第1轮"},
        {"role": "assistant", "content": "第1轮答"},
        {"role": "user", "content": "第2轮"},
    ]
    summary = "摘要：用户在做项目"
    assembled = assemble(messages, summary, keep_recent=2)
    assert assembled[0]["role"] == "system"  # ③ system 最前
    assert assembled[1]["role"] == "user"  # ① 摘要块 user 角色
    assert "摘要：用户在做项目" in assembled[1]["content"]
    # ② 最近消息保留且 role 交替合法
    roles = [m["role"] for m in assembled[1:]]
    assert roles[0] == "user"
    for i in range(1, len(roles)):
        assert roles[i] != roles[i - 1], f"连续同 role: {roles}"


def test_assemble_no_recent_kept() -> None:
    """keep_recent=0 → 只剩 system + 摘要（全部压掉）。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "内容"},
    ]
    assembled = assemble(messages, "摘要", keep_recent=0)
    assert len(assembled) == 2
    assert assembled[0]["role"] == "system"
    assert assembled[1]["role"] == "user"
    assert "摘要" in assembled[1]["content"]


def test_assemble_recent_starts_at_group_boundary() -> None:
    """L1②：最近保留从组边界开始——tool 结果不能作为组开头。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "早期"},
        {"role": "assistant", "content": "早期答"},
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "content": "结果", "tool_call_id": "c1"},
        {"role": "assistant", "content": "最终答案"},
    ]
    # keep_recent=4 → 最近 4 条是 [user查一下, assistant tc, tool, assistant]
    # 但 group 边界：tool 必须跟 assistant(tc) 成组——从 [user查一下] 开始合法
    summary = "摘要"
    assembled = assemble(messages, summary, keep_recent=4)
    recent = assembled[2:]
    assert recent[0]["role"] != "tool"  # 不从 tool 结果开始
    # 组内完整性：assistant(tool_calls) 后面必须有 tool 结果
    for i, m in enumerate(recent):
        if m.get("tool_calls"):
            assert recent[i + 1]["role"] == "tool"  # 成对
