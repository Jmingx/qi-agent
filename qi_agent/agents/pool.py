"""AgentPool：执行者池（轻壳——工厂 + 并发治理，不复用）。

方案 2026-08-24-AgentPool（用户拍板）：
- agent 复用是伪需求——make_agent 极轻（无网络 I/O，仅配置装配），
  维护池状态（借出/归还/健康检查）得不偿失
- 池 = Semaphore（并发上限）+ Factory（统一派活），不是 ThreadPool 式复用
- acquire() → 检查 max_workers → make_agent → 跑任务 → release（即弃）

两种用途（场景矩阵）：
- 主对话：acquire(主 context) 拿主执行者（串行，本质是工厂入口）
- 子任务：acquire(None) 新建子 context + 执行者（并行，并发治理生效）

类比（Java）：Executors 的工厂 + 信号量，不是线程池的 worker 复用
（线程重才复用；agent 轻，即建即用）。
"""

import threading
import time

from typing import Any

from qi_agent.agents.agent import Agent
from qi_agent.context.context import AgentContext
# 注意：不模块级 import factory.make_agent——factory → agent_manager → pool
# 循环导入。make_agent 在 acquire() 内延迟 import（pool 是轻壳，工厂延迟注入）。


class AgentPool:
    """执行者池：acquire（限并发创建）/ release（回收额度）。"""

    def __init__(self, max_workers: int = 3) -> None:
        self.max_workers = max_workers
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        """当前活跃执行者数（可观测——/status 可显示并发占用）。"""
        with self._lock:
            return self._active

    def acquire(self, context: AgentContext | None = None,
                type: str = "standard") -> Agent:
        """取执行者（检查并发上限，超限等待）。

        Args:
            context: 数据载体——None = 新建子任务 context（独立隔离）；
                传入 = 绑定该 context（主对话/复用场景）
            type: 执行者类型（透传 make_agent——可插拔）

        Returns:
            Agent 执行者（无状态——数据全在 context，完成即弃）
        """
        # 并发上限（D3：等待而非拒绝——任务会完成，拒绝要调用方重试）
        while True:
            with self._lock:
                if self._active < self.max_workers:
                    self._active += 1
                    break
            time.sleep(0.05)  # 超限等待（轮询——简单可靠）
        try:
            if context is None:
                # 子任务：新建独立 context（隔离，对齐 subagent 哲学）
                context = AgentContext(persist=False)
            from qi_agent.agents.factory import make_agent  # 延迟 import（防循环）

            return make_agent(context, type=type)
        except Exception:
            # 创建失败：回收额度（不泄漏）
            with self._lock:
                self._active -= 1
            raise

    def release(self, agent: Any) -> None:
        """任务完成，归还额度（agent 即弃——不复用）。

        try/finally 语义：调用方保证即使异常也 release（额度不泄漏）。
        """
        with self._lock:
            self._active = max(0, self._active - 1)
