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
    """steer 并发 append + 消费（drain）——复合操作竞争。"""
    ctx = AgentContext()
    n_threads = 10
    per_thread = 100
    consumed = []

    def stepper():
        for i in range(per_thread):
            ctx.steer_queue.append(f"s{i}")

    def drainer():
        for _ in range(50):
            if ctx.steer_queue:
                # 复合：检查 + 弹出（可能交错）
                consumed.append(ctx.steer_queue.pop(0))

    threads = [threading.Thread(target=stepper) for _ in range(n_threads)]
    d = threading.Thread(target=drainer)
    threads.append(d)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = n_threads * per_thread
    remaining = len(ctx.steer_queue)
    print(f"steer: 生成{total} 消费{len(consumed)} 剩余{remaining} "
          f"总={len(consumed) + remaining}")
    # pop(0) 是 O(n) 且可能 IndexError（两个 drainer 竞争）——这里单 drainer
    assert len(consumed) + remaining == total, "steer 队列元素丢失"
