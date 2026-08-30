"""AgentPool 超时测试（2026-08-30：acquire 加 timeout——不无限阻塞）。"""

import time

from qi_agent.agents.pool import AgentPool


def test_acquire_timeout_returns_none() -> None:
    """额度满 + 超时 → acquire 返回 None（不无限阻塞）。"""
    pool = AgentPool(max_workers=1)
    # 占满额度
    a1 = pool.acquire()
    assert a1 is not None
    # 第二个 acquire 超时（0.1s）→ None
    t0 = time.monotonic()
    a2 = pool.acquire(timeout=0.1)
    elapsed = time.monotonic() - t0
    assert a2 is None
    assert elapsed < 1.0  # 没无限等
    pool.release(a1)


def test_acquire_succeeds_after_release() -> None:
    """额度释放后 acquire 立即成功（timeout 不影响正常路径）。"""
    pool = AgentPool(max_workers=1)
    a1 = pool.acquire()
    pool.release(a1)
    a2 = pool.acquire(timeout=1.0)
    assert a2 is not None
    pool.release(a2)
