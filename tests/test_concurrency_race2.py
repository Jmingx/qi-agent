"""复合操作竞争测试：读-校验-写（跨多条字节码）在多线程下交错。

GIL 保护单条字节码，但【读-校验-写】是复合操作——线程可在中间切换。
场景模拟：提炼线程读 messages[-20:] 快照 vs 主线程同时 append。
"""

import threading
import time

from qi_agent.context.context import AgentContext


def test_snapshot_while_appending() -> None:
    """一个线程读快照（[-20:]），另一个线程同时 append——可能读到半状态。"""
    ctx = AgentContext()
    ctx.messages = [{"role": "system", "content": "sys"}]
    stop = threading.Event()
    snapshots = []

    def writer():
        i = 0
        while not stop.is_set():
            ctx.messages.append({"role": "user", "content": f"w{i}"})
            i += 1

    def reader():
        while not stop.is_set():
            snap = ctx.messages[-20:]  # 快照（读时可能被 writer 修改）
            snapshots.append(len(snap))

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    time.sleep(0.2)
    stop.set()
    w.join()
    r.join()

    # 快照长度应总是 1..20（不会越界或负数）——列表切片在 GIL 下安全
    # 但【读到的内容可能不一致】（半写状态）——这是设计问题不是崩溃
    print(f"快照样本: {snapshots[:5]} ... 共 {len(snapshots)} 次")
    # 切片安全（GIL），但内容一致性无法保证——文档化
    assert all(1 <= s <= 20 for s in snapshots), "快照越界"


def test_steer_queue_race() -> None:
    """steer 并发投递 + 消费（drain）——零丢失（方案 2026-08-29 验收 1）。

    v3（2026-08-29）：steer 走 mailbox（STEER 消息）——put/get 原子。
    唯一入口 = manager.steer（2026-08-29 收敛）。验证生成=消费（零丢失）。
    """
    from qi_agent.agents.agent_manager import AgentManager

    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")
    n_threads = 10
    per_thread = 100
    consumed: list[str] = []

    def stepper():
        for i in range(per_thread):
            mgr.steer(ctx.id, f"s{i}")

    def drainer():
        for _ in range(50):
            consumed.extend(ctx.drain_steer())  # 取空（原子）

    threads = [threading.Thread(target=stepper) for _ in range(n_threads)]
    d = threading.Thread(target=drainer)
    threads.append(d)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 生产者结束后清尾（drain 可能错过最后一批 + Dispatcher 异步搬运）
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        consumed.extend(ctx.drain_steer())
        if len(consumed) >= n_threads * per_thread:
            break
        time.sleep(0.01)

    total = n_threads * per_thread
    print(f"steer: 生成{total} 消费{len(consumed)}")
    assert len(consumed) == total, "steer 指令丢失（queue.Queue 应零丢失）"
