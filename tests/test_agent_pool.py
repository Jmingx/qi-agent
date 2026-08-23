"""AgentPool 测试（方案 2026-08-24-AgentPool Phase 2）。

验证：轻壳（工厂 + 并发治理，不复用）——acquire 超限等待 + release 回收。
"""

import threading
import time

import pytest

from qi_agent.agents.pool import AgentPool


@pytest.fixture()
def fake_key(monkeypatch):
    """mock API key（make_agent 读）。"""
    import qi_agent.agents.factory as factory

    monkeypatch.setattr(factory, "load_api_key", lambda: "sk-test")


def test_acquire_returns_agent(fake_key) -> None:
    """acquire 创建执行者（绑定传入 context）。"""
    from qi_agent.context.context import AgentContext

    pool = AgentPool(max_workers=2)
    ctx = AgentContext()
    agent = pool.acquire(ctx)
    assert agent is not None
    assert agent.context is ctx
    assert hasattr(agent, "chat")
    pool.release(agent)


def test_acquire_creates_context_when_none(fake_key) -> None:
    """acquire(None) 新建子任务 context（独立隔离）。"""
    pool = AgentPool(max_workers=2)
    agent = pool.acquire(None)
    assert agent.context is not None
    assert agent.context.id  # 独立 context
    pool.release(agent)


def test_concurrent_limit_waits(fake_key) -> None:
    """并发超限等待：max_workers=1 时第二个 acquire 阻塞直到 release。"""
    pool = AgentPool(max_workers=1)
    a1 = pool.acquire(None)

    acquired = []
    t = threading.Thread(target=lambda: acquired.append(pool.acquire(None)))
    t.start()
    time.sleep(0.2)
    assert not acquired  # 第二个还在等（超限）

    pool.release(a1)  # 释放额度 → 第二个拿到
    t.join(timeout=3)
    assert len(acquired) == 1
    pool.release(acquired[0])


def test_release_recovers_quota(fake_key) -> None:
    """release 回收额度（try/finally 语义——异常也不泄漏）。"""
    pool = AgentPool(max_workers=2)
    a1 = pool.acquire(None)
    a2 = pool.acquire(None)
    pool.release(a1)
    pool.release(a2)
    assert pool.active_count == 0


def test_active_count_tracks(fake_key) -> None:
    """active_count 跟踪活跃执行者数。"""
    pool = AgentPool(max_workers=3)
    a1 = pool.acquire(None)
    a2 = pool.acquire(None)
    assert pool.active_count == 2
    pool.release(a1)
    assert pool.active_count == 1
    pool.release(a2)
    assert pool.active_count == 0
