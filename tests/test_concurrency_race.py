"""并发压力测试：暴露 context/events 无锁的数据竞争（2026-08-28）。

系统性排查并发问题（用户要求）——先证明问题存在，再修。
场景：多线程并发读写 context.messages/turn + events.emit。
"""

import threading

from qi_agent.context.context import AgentContext


def test_turn_increment_race() -> None:
    """多线程并发 turn += 1 → 丢失更新（无锁必现）。"""
    ctx = AgentContext()
    n_threads = 20
    per_thread = 50

    def worker():
        for _ in range(per_thread):
            ctx.turn += 1  # 复合操作（读-加-写）

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = n_threads * per_thread  # 1000
    print(f"期望 turn={expected} 实际 turn={ctx.turn} 丢失={expected - ctx.turn}")
    # 不强制断言（GIL 下可能偶发）——打印暴露，修复后应=期望
    assert ctx.turn == expected, (
        f"并发丢失更新: 期望{expected} 实际{ctx.turn}（无锁）")


def test_messages_append_race() -> None:
    """多线程并发 append messages → 可能丢失（列表竞争）。"""
    ctx = AgentContext()
    n_threads = 20
    per_thread = 50

    def worker():
        for i in range(per_thread):
            ctx.messages.append({"role": "user", "content": f"t{i}"})

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = n_threads * per_thread  # 1000
    print(f"期望消息={expected} 实际消息={len(ctx.messages)} "
          f"丢失={expected - len(ctx.messages)}")
    assert len(ctx.messages) == expected, (
        f"并发追加丢失: 期望{expected} 实际{len(ctx.messages)}（无锁）")
