"""滑动窗口裁剪（阶段 B1，方案 2026-08-22）：token 预算 + 消息组 + 交替。

裁剪算法（第一版 4 算法之一，详见方案/备注）：
1. token 预算：数 token 不数条数——从最旧开始删，直到剩余 <= 预算
2. 消息组裁剪：assistant 的 tool_calls 消息 + 它的所有 tool 结果 = 一组，
   整组进整组出（拆散 = API 协议错误——两条死线之一）
3. role 交替：裁剪后第一个非 system 消息若为 assistant → 注入 user
   anchor（"[早期对话已裁剪]"）——既满足协议交替，又告知模型
4. system（含 sticky）永不裁——只动历史
"""

from qi_agent.context.estimator import estimate_tokens

# 裁剪后注入的 anchor 消息（对齐 Hermes：模型知道历史被裁了，不困惑）
ANCHOR_TEXT = "[早期对话已裁剪——如需回忆早期内容请说明]"


def _group_messages(messages: list[dict]) -> list[list[dict]]:
    """把消息切成组：assistant(tool_calls) + 后续 tool 结果 = 一组。

    普通消息（user / 无 tool_calls 的 assistant）自成一组。
    tool 结果消息必须加入"打开的组"（其 assistant tool_calls 之后）——
    孤儿 tool 结果防御性自成组（正常流程不应发生）。
    """
    groups: list[list[dict]] = []
    open_group: list[dict] | None = None
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            # tool 结果：加入打开的组（assistant tool_calls 后）
            if open_group is None:
                open_group = [msg]  # 防御：孤儿 tool 结果
            else:
                open_group.append(msg)
            continue
        if open_group is not None:
            groups.append(open_group)
            open_group = None
        open_group = [msg]
        if role == "assistant" and msg.get("tool_calls"):
            continue  # 组保持打开（等后续 tool 结果）
        groups.append(open_group)
        open_group = None
    if open_group is not None:
        groups.append(open_group)
    return groups


def _msg_tokens(msg: dict) -> int:
    """单条消息 token 估算（content 缺失时估算 tool_calls JSON）。"""
    content = msg.get("content")
    if content:
        return estimate_tokens(str(content))
    if msg.get("tool_calls"):
        import json
        return estimate_tokens(json.dumps(msg["tool_calls"], ensure_ascii=False))
    return 1  # 空消息保底


def trim_messages(messages: list[dict], max_tokens: int) -> tuple[list[dict], int]:
    """按 token 预算裁剪最旧消息组。

    Args:
        messages: 当前消息历史（含 system）
        max_tokens: token 预算（超出才裁剪）

    Returns:
        (裁剪后消息, 裁剪的组数)——未超预算时原样返回（0 组）。
    """
    # system 永不裁（含 sticky）；只对非 system 历史分组
    system_msgs = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    if not rest:
        return messages, 0

    groups = _group_messages(rest)
    # 从最新往旧累积保留组（滑动窗口 = 保最近），直到预算用尽
    kept_groups: list[list[dict]] = []
    used = 0
    for group in reversed(groups):
        group_tokens = sum(_msg_tokens(m) for m in group)
        # 预算满且已至少保留一组 → 停止（保底一组，超预算也保留）
        if used + group_tokens > max_tokens and kept_groups:
            break
        kept_groups.append(group)
        used += group_tokens
    kept_groups.reverse()

    trimmed = len(groups) - len(kept_groups)
    if trimmed <= 0:
        return messages, 0

    result = system_msgs + [m for g in kept_groups for m in g]
    # role 交替死线：裁剪后第一个非 system 消息若为 assistant →
    # 注入 user anchor（协议要求交替 + 告知模型历史被裁）
    if result[len(system_msgs):] and result[len(system_msgs)].get("role") == "assistant":
        result.insert(len(system_msgs), {"role": "user", "content": ANCHOR_TEXT})
    return result, trimmed
