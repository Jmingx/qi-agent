"""异步压缩器（方案 2026-08-23 二期）：后台线程 + 快照隔离 + 新鲜度。

设计（并发安全三条防线）：
① 快照隔离：后台线程只读提交时的消息副本，生成压缩结果存快照——
   agent.messages 永远单线程写（无竞态）
② 新鲜度校验：快照生成时的消息组数 vs 当前——增长超阈值（默认 3 组）
   丢弃（压缩旧快照 = 丢后续对话，宁可重新压）
③ 单任务锁：同时只允许一个压缩任务（防堆积）；失败静默（下轮重试）
"""

import threading

from qi_agent.context.compressor import assemble, compress_messages
from qi_agent.context.window import _group_messages


class AsyncCompressor:
    """后台压缩 worker：request 提交 → 后台生成快照 → take_if_fresh 取用。"""

    def __init__(self, summarizer, keep_recent: int = 10,
                 max_growth: int = 3) -> None:
        self._summarizer = summarizer
        self._keep_recent = keep_recent
        self._max_growth = max_growth  # 新鲜度阈值：快照后消息组增长上限
        self._lock = threading.Lock()
        self._running = False          # 单任务锁（有任务进行中）
        self._snapshot: dict | None = None  # {"messages", "source_groups", "ready"}

    # ── 主线程接口 ────────────────────────────────────────────────────────

    def request(self, messages: list[dict]) -> bool:
        """提交压缩任务（单任务锁：进行中则跳过，返回是否提交）。"""
        with self._lock:
            if self._running:
                return False  # 已有任务进行中（防堆积）
            self._running = True
            snapshot = [dict(m) for m in messages]  # 深拷贝（快照隔离）
        thread = threading.Thread(
            target=self._work, args=(snapshot,), daemon=True,
            name="async-compress",
        )
        thread.start()
        return True

    def take_if_fresh(self, current_messages: list[dict]) -> list[dict] | None:
        """取用压缩快照（就绪 + 新鲜）；不满足则丢弃。

        Args:
            current_messages: 当前消息（对比新鲜度）

        Returns:
            压缩后消息（快照被取走）；None = 无快照/不新鲜（已丢弃）
        """
        with self._lock:
            snap = self._snapshot
            self._snapshot = None
        if not snap or not snap.get("ready"):
            return None
        # 新鲜度：当前组数 - 快照源组数 ≤ max_growth（否则丢弃）
        cur_groups = len(_group_messages(
            [m for m in current_messages if m.get("role") != "system"]))
        if cur_groups - snap["source_groups"] > self._max_growth:
            return None  # 对话已继续太多轮——快照过期，丢弃
        return snap["messages"]

    def is_busy(self) -> bool:
        """是否有压缩任务进行中（插件跳过 compress 策略防双重压缩）。"""
        with self._lock:
            return self._running

    def wait_idle(self, timeout: float = 5.0) -> None:
        """等待任务完成（测试用：轮询 is_busy）。"""
        import time

        deadline = time.monotonic() + timeout
        while self.is_busy() and time.monotonic() < deadline:
            time.sleep(0.02)

    # ── 后台线程 ──────────────────────────────────────────────────────────

    def _work(self, snapshot: list[dict]) -> None:
        """后台：切分 → 摘要 → 组装快照（只读副本，不碰 agent.messages）。"""
        try:
            early, _ = compress_messages(snapshot, keep_recent=self._keep_recent)
            if not early:
                return  # 无可压缩的早期历史
            summary = self._summarizer(early)
            compressed = assemble(snapshot, summary, keep_recent=self._keep_recent)
            with self._lock:
                self._snapshot = {
                    "messages": compressed,
                    "source_groups": len(_group_messages(
                        [m for m in snapshot if m.get("role") != "system"])),
                    "ready": True,
                }
        except Exception:
            # 失败静默（下轮重试）；快照保持 None
            pass
        finally:
            with self._lock:
                self._running = False
