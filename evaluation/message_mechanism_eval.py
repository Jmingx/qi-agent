"""Message 机制端到端测评（2026-08-30）。

验证维度（真实链路——不 mock Dispatcher/LLM 核心路径）：
  ① 对话投递：主 → 子（MESSAGE）子 drain_messages 消费
  ② 控制指令：主 → 子（STEER）子 drain_steer 消费（sender=调用者）
  ③ 结果回传：子完成 → 父（RESULT）父 mailbox 收到
  ④ 失败通知：意外崩溃 → 父收 RESULT(failed)
  ⑤ 退信回执：未知 target → 发送方收 NACK
  ⑥ 并发零丢失：100 条并发 send → 全部到达
  ⑦ 日志佐证：message.log/run.log/events.log 有对应记录

跑法：PYTHONPATH= .venv/Scripts/python evaluation/message_mechanism_eval.py
"""

import sys
import time

sys.path.insert(0, ".")

from qi_agent.agents.agent_manager import AgentManager  # noqa: E402
from qi_agent.agents.mailbox import Message, MessageType  # noqa: E402
from qi_agent.context.context import AgentContext  # noqa: E402

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
        time.sleep(0.01)
    return None


def _fast_client():
    """Fake LLM（子 agent 用——快速返回，测消息机制不测 LLM）。"""
    class C:
        def chat(self, messages, tools=None):
            from qi_agent.llm import ChatResult
            return ChatResult(content="ok", tool_calls=[],
                              assistant_message={"role": "assistant",
                                                 "content": "ok"},
                              usage=None)

        def chat_stream(self, messages, tools=None, on_delta=None):
            return self.chat(messages, tools)
    return C()


def main() -> int:
    print("═" * 50)
    print("Message 机制端到端测评")
    print("═" * 50)

    mgr = AgentManager()
    parent = AgentContext(context_id="ctx_parent")
    mgr.register(parent, role="main")

    # ── ① 对话投递（MESSAGE）────────────────────────────────────────
    print("\n[① 对话投递 MESSAGE]")
    sub = mgr.spawn("测对话投递", parent_id="ctx_parent",
                    client_factory=_fast_client)
    assert mgr.send_message(sub.id, "第一轮指导", sender_id="ctx_parent")
    got = wait_for(lambda: sub.drain_messages())
    check("子收到对话投递", got == ["第一轮指导"], str(got))

    # ── ② 控制指令（STEER + sender）──────────────────────────────────
    print("\n[② 控制指令 STEER]")
    assert mgr.steer(sub.id, "改方向", sender_id="ctx_parent")
    msgs = wait_for(lambda: sub.mailbox.drain())
    check("子收到 STEER", msgs and msgs[0].type == MessageType.STEER
          and msgs[0].data == "改方向", str(msgs))
    check("STEER sender=调用者 context_id",
          msgs and msgs[0].sender == "ctx_parent",
          str(msgs[0].sender if msgs else None))

    # ── ③ 结果回传（RESULT——子完成投父）─────────────────────────────
    print("\n[③ 结果回传 RESULT]")
    wait_for(lambda: parent.mailbox.inbox.qsize() >= 1)
    results = parent.mailbox.drain()
    check("父收到子结果", any(m.type == MessageType.RESULT for m in results),
          str([m.type.value for m in results]))

    # ── ④ 失败通知（意外崩溃 → 父收 RESULT failed）───────────────────
    print("\n[④ 失败通知]")
    bad = mgr.spawn("必失败", parent_id="ctx_parent",
                    client_factory=lambda: (_ for _ in ()).throw(
                        RuntimeError("装配级崩溃")))
    assert bad is not None
    wait_for(lambda: parent.mailbox.inbox.qsize() >= 1)
    msgs = parent.mailbox.drain()
    failed = [m for m in msgs if m.type == MessageType.RESULT
              and (m.data or {}).get("status") == "failed"]
    check("父收到失败 RESULT", len(failed) >= 1, str(len(failed)))
    check("失败含错误信息", any("异常" in (m.data or {}).get("error", "")
                               for m in failed))

    # ── ⑤ 退信回执（未知 target → NACK）──────────────────────────────
    print("\n[⑤ 退信回执 NACK]")
    ghost = AgentContext(context_id="ctx_ghost")
    mgr.register(ghost, role="main")
    mgr.dispatcher.send(Message(sender="ctx_ghost", target="nobody",
                                type=MessageType.STEER, data="x"))
    got = wait_for(lambda: ghost.mailbox.drain())
    check("发送方收到 NACK", got and got[0].type == MessageType.NACK,
          str([m.type.value for m in got] if got else None))
    check("NACK 带原消息信息",
          got and got[0].data.get("original_target") == "nobody"
          and got[0].data.get("reason") == "unknown_target",
          str(got[0].data if got else None))

    # ── ⑥ 并发零丢失 ─────────────────────────────────────────────────
    print("\n[⑥ 并发零丢失]")
    recv = []
    N = 100
    for i in range(N):
        mgr.dispatcher.send(Message(sender="ctx_ghost", target="ctx_ghost",
                                    type=MessageType.NOTIFY, data=str(i)))

    def collect():
        got = ghost.mailbox.drain()
        recv.extend(m.data for m in got if m.type == MessageType.NOTIFY)
        return len(recv) >= N

    wait_for(collect, timeout=8.0)
    check("100 条并发全到达", len(recv) >= N, f"收到 {len(recv)}/{N}")

    # ── ⑦ 日志佐证 ───────────────────────────────────────────────────
    print("\n[⑦ 日志佐证]")
    import os
    log_dir = os.path.expanduser("~/.qi-agent/logs")
    for f in ("message.log", "run.log", "events.log", "rpc.log"):
        check(f"{f} 存在且非空",
              os.path.exists(os.path.join(log_dir, f))
              and os.path.getsize(os.path.join(log_dir, f)) > 0)

    print("\n" + "═" * 50)
    print(f"结果: {PASS} 通过 / {FAIL} 失败")
    print("═" * 50)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
