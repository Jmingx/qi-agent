"""策略基类（方案 2026-08-23-上下文压缩策略链）：ContextStrategy + ContextInfo。

设计：策略模式 + 责任链 + 注册表——每个上下文改写算法是一个策略类
（1 策略 1 文件，对齐"1 文件 1 工具"），context_manager 插件纯编排
（按 config.chain 顺序执行，should_apply 判断 + apply 改写 + 消费即停）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass
class ContextInfo:
    """策略上下文：真实 usage / 配置 / 依赖注入。

    prompt_tokens 来自 post-llm 采集的 response 真实 usage（用户要求
    2026-08-22：token 消耗不估算）；summarizer 依赖注入（测试 mock）。
    """

    prompt_tokens: int = 0          # 最近一次响应的真实 prompt tokens
    window: int = 128_000           # 上下文窗口
    threshold: float = 0.7          # 压缩触发阈值（窗口占比）
    keep_recent: int = 10           # 压缩保留的最近消息组数
    budget: int | None = 100_000    # 裁剪预算（None 禁用）
    summarizer: Callable | None = None  # 摘要器（压缩策略用）
    chain_name: str = ""            # 当前策略链名（调试）
    step: int = 0                   # 当前 step（调试）


class ContextStrategy(ABC):
    """上下文改写策略基类（统一接口，插件只依赖此抽象）。"""

    name: str = ""  # 策略名（config.chain 引用；注册表键）

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    def should_apply(self, ctx: ContextInfo) -> bool:
        """是否要处理（基于真实 usage 等上下文信息）。"""

    @abstractmethod
    def apply(self, messages: list[dict], ctx: ContextInfo
              ) -> tuple[list[dict], bool]:
        """执行改写。

        Returns:
            (新消息, 是否消费)——消费=True 时责任链停止（处理完就停）
        """
