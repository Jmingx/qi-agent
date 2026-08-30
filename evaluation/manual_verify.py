"""手工触发验证用例集（2026-08-30——验证 event/message/子agent/记忆/压缩/steer）。

用法：
  ① 脚本路径（推荐——可重复、输出验证点）：
     PYTHONPATH= .venv/Scripts/python evaluation/manual_verify.py [case_id]
     不带 case_id = 跑全部；带 = 只跑指定（如 manual_verify.py 3）

  ② CLI 对照路径（真实交互）：
     见每个用例的「CLI 对照」——启动 qi-agent 后输入对应命令

  验证时配合日志（4 份）：
    tail -f ~/.qi-agent/logs/events.log    # 事件流
    tail -f ~/.qi-agent/logs/message.log   # 消息流
    tail -f ~/.qi-agent/logs/run.log       # 执行流
"""

import sys
import time

sys.path.insert(0, ".")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def wait_for(fn, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = fn()
        if r:
            return r
        time.sleep(0.02)
    return None


def _fast_client(seen=None):
    """Fake LLM（快速返回——验证机制不验证 LLM）。"""
    class C:
        def __init__(self):
            self.seen = seen if seen is not None else []

        def chat(self, messages, tools=None):
            from qi_agent.llm import ChatResult
            self.seen.append(list(messages))
            return ChatResult(content="ok", tool_calls=[],
                              assistant_message={"role": "assistant",
                                                 "content": "ok"},
                              usage=None)

        def chat_stream(self, messages, tools=None, on_delta=None):
            return self.chat(messages, tools)
    return C()


# ── 1. 事件（events）────────────────────────────────────────────────────
def case_event():
    print("\n═══ 用例 1：事件（EventBus emit/on）═══")
    print("  CLI 对照：正常对话即可（events.log 会记录每个 emit）")
    from qi_agent.context.context import AgentContext

    ctx = AgentContext(context_id="ctx_evt")
    seen = []
    ctx.events.on("agent/turn-start", lambda **kw: seen.append(kw))
    ctx.events.emit("agent/turn-start", user_input="你好")
    check("事件触发（监听者收到）", len(seen) == 1)
    check("事件 payload 完整", seen[0]["user_input"] == "你好")
    # 无监听者事件（emit 不报错——广播语义）
    ctx.events.emit("agent/final-answer", content="ok")
    check("无监听者 emit 不崩（广播语义）", True)
    print("  验证日志：tail -n 5 ~/.qi-agent/logs/events.log"
          "（应见 emit context=ctx_evt event=agent/turn-start）")


# ── 2. 消息（message 机制）─────────────────────────────────────────────
def case_message():
    print("\n═══ 用例 2：消息（邮局 send/deliver/NACK）═══")
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.agents.mailbox import Message, MessageType
    from qi_agent.context.context import AgentContext

    mgr = AgentManager()
    a = AgentContext(context_id="ctx_a")
    b = AgentContext(context_id="ctx_b")
    mgr.register(a, role="main")
    mgr.register(b, role="main")
    # ① 正常投递：a → b
    mgr.send_message(b.id, "你好B", sender_id="ctx_a")
    got = wait_for(lambda: b.drain_messages())
    check("正常投递（a→b 收到）", got == ["你好B"], str(got))
    # ② 退信：未知 target → NACK 回执
    mgr.dispatcher.send(Message(sender="ctx_a", target="nobody",
                                type=MessageType.STEER, data="x"))
    nack = wait_for(lambda: a.mailbox.drain())
    check("退信 NACK（未知 target 回执）",
          nack and nack[0].type == MessageType.NACK, str(nack))
    print("  验证日志：tail -n 10 ~/.qi-agent/logs/message.log"
          "（应见 deliver + undeliverable + nack-deliver 成对）")


# ── 3. 子 agent（spawn 生命周期）──────────────────────────────────────
def case_subagent():
    print("\n═══ 用例 3：子 agent（spawn → 结果回传）═══")
    print("  CLI 对照：/delegate 查一下项目用了哪些 Python 库")
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext

    mgr = AgentManager()
    parent = AgentContext(context_id="ctx_parent")
    mgr.register(parent, role="main")
    sub = mgr.spawn("分析需求", parent_id="ctx_parent",
                    client_factory=_fast_client)
    check("spawn 返回 AgentContext（统一类型）", type(sub).__name__ == "AgentContext")
    check("子 agent 注册进 manager", sub.id in mgr.contexts)
    # 等结果回传（RESULT → 父 mailbox）
    got = wait_for(lambda: parent.mailbox.drain())
    check("结果回传（父收到 RESULT）",
          got and any(m.type.value == "result" for m in got), str(got))
    print("  验证日志：tail -n 3 ~/.qi-agent/logs/run.log"
          "（应见 subagent-complete context=agt_xxx）")


# ── 4. 子 agent 内容获取（多轮指导/steer 子 agent）────────────────────
def case_subagent_content():
    print("\n═══ 用例 4：子 agent 内容获取（对话投递 + steer 子）═══")
    print("  CLI 对照：/delegate 查天气 → 补充「重点看温度」→ 看子是否响应")
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext

    mgr = AgentManager()
    parent = AgentContext(context_id="ctx_parent")
    mgr.register(parent, role="main")
    seen = []
    sub = mgr.spawn("调研", parent_id="ctx_parent",
                    client_factory=_fast_client(seen))
    # ① 对话投递（MESSAGE）+ ② steer（STEER）——等两条都到 inbox
    mgr.send_message(sub.id, "补充背景：用户是新手", sender_id="ctx_parent")
    mgr.steer(sub.id, "重点查性能", sender_id="ctx_parent")
    wait_for(lambda: sub.mailbox.inbox.qsize() >= 2)
    # ③ 子 agent 内容注入 LLM（consume_mailbox 合并成一条 user）
    got = sub.consume_mailbox([])
    check("子消费合并注入（消息+steer 一条 user）",
          got and len(got) == 1
          and "补充背景" in str(got[0]) and "重点查性能" in str(got[0]),
          str(got))
    print("  验证日志：message.log（send/deliver）+ events.log（pre-step）")


# ── 5. 记忆（主动记忆提炼）────────────────────────────────────────────
def case_memory():
    print("\n═══ 用例 5：记忆（/remember + /memory + 主动提炼）═══")
    print("  CLI 对照：")
    print("    /remember 我喜欢打篮球     ← 会话内 + 跨会话")
    print("    /memory                    ← 查看记忆")
    print("    （主动提炼：连续对话 10 轮后自动触发——run.log 看提炼）")
    from qi_agent.storage.memory_store import MemoryStore

    store = MemoryStore()
    # ① /remember 路径（sticky + 跨会话）
    store.add_memory("我喜欢打篮球")
    text = store.read_memory()
    check("记忆写入（/remember 等价）", "喜欢打篮球" in text, text[:50])
    # ② 主动提炼触发条件（间隔变量）
    from qi_agent.context.context import AgentContext
    ctx = AgentContext()
    check("提炼间隔=10 轮", getattr(ctx, "memory_extract_interval", None) == 10)
    print("  验证日志：~/.qi-agent/logs/run.log 搜 memory（提炼记录）")


# ── 6. 上下文压缩（/compact）──────────────────────────────────────────
def case_compact():
    print("\n═══ 用例 6：上下文压缩（/compact + pre-step 策略链）═══")
    print("  CLI 对照：")
    print("    /status           ← 看当前消息数")
    print("    /compact          ← 强制压缩")
    print("    /context          ← 看压缩后构成")
    print("  脚本验证：context_manager 插件存在 + pre-step 注册")
    from qi_agent.plugins import builtin  # noqa: F401  触发自注册
    from qi_agent.plugins.registry import get_plugin_names

    names = get_plugin_names()
    check("context_manager 插件注册", "context_manager" in names, str(names))
    check("approval_gate 插件注册", "approval_gate" in names)
    check("memory 插件注册", "memory" in names)
    print("  验证日志：events.log 搜 pre-step（每轮触发——策略链执行）")


# ── 7. steer（主 agent 引导）──────────────────────────────────────────
def case_steer():
    print("\n═══ 用例 7：steer（外部/父 → 主 agent 引导）═══")
    print("  CLI 对照：")
    print("    /status           ← 确认主 agent 在跑")
    print("    （外部 steer 主 agent：脚本路径验证——CLI 暂未接）")
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext
    from qi_agent.agents.agent import Agent

    mgr = AgentManager()
    ctx = AgentContext(context_id="ctx_main")
    mgr.register(ctx, role="main")
    client = _fast_client()
    agent = Agent(client, system_prompt="", max_turns=2, context=ctx)
    # 外部 steer 主 agent（sender=unknown——用户）
    mgr.steer(ctx.id, "改方向：少用工具", sender_id="unknown")
    time.sleep(0.3)
    # 主 agent 下轮 chat——consume_mailbox 注入
    agent.chat("你好")
    joined = " ".join(str(m) for m in client.seen[-1])
    check("主 agent 收到 steer 并注入",
          "[引导] 改方向" in joined, joined[:100])
    user_msgs = [m for m in client.seen[-1]
                 if isinstance(m, dict) and m.get("role") == "user"]
    check("合并成一条 user（防连续 user）", len(user_msgs) >= 1
          and "[引导] 改方向" in user_msgs[-1].get("content", ""),
          str(user_msgs))
    print("  验证日志：message.log（deliver steer）+ events.log（pre-step）")


CASES = {
    "1": case_event, "2": case_message, "3": case_subagent,
    "4": case_subagent_content, "5": case_memory, "6": case_compact,
    "7": case_steer,
}


def main() -> int:
    global PASS, FAIL
    print("═" * 55)
    print("qi-agent 手工验证用例集（7 项能力）")
    print("═" * 55)
    case_id = sys.argv[1] if len(sys.argv) > 1 else None
    if case_id:
        CASES[case_id]()
    else:
        for cid in sorted(CASES):
            CASES[cid]()
    print("\n" + "═" * 55)
    print(f"结果: {PASS} 通过 / {FAIL} 失败"
          + ("（单用例" + case_id + "）" if case_id else "（全部）"))
    print("═" * 55)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
