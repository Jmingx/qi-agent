"""轻量事件总线：emit（通知）/ waterfall（改写）/ bail（决策）三种分发模式。

设计（方案 docs/plans/2026-08-18-事件化改造方案.md，参考 DSH/Cordis）：
- 一套注册接口（on），按分发模式区分语义——覆盖"纯事件"到"纯钩子"谱系
- emit:    广播通知，忽略返回值（状态/统计/日志）
- waterfall: 返回值喂给下一个监听者，最终结果回传发布者（数据改写）
- bail:    第一个非 None 返回即停止（决策/拦截）

对齐 Cordis 设计（vendor/cordis/src/events.ts）：
- 监听者带 priority（大者先执行），同 priority 按注册顺序（稳定排序）
- 事件名用 agent/* 命名空间（对齐 DSH：agent/pre-step、agent/tool-call 等）
"""

from typing import Any, Callable


class EventBus:
    """事件总线：插件注册监听器，主循环在事件点分发。"""

    def __init__(self) -> None:
        self._listeners: dict[str, list[_Listener]] = {}

    def on(self, event: str, handler: Callable, priority: int = 0) -> None:
        """注册监听器。

        Args:
            event: 事件名（agent/turn-start 等命名空间格式）
            handler: 监听函数（接收 **data；waterfall 额外接收 data 首参）
            priority: 执行优先级，数值大先执行（默认 0）
        """
        self._listeners.setdefault(event, []).append(_Listener(handler, priority))
        # 按优先级降序排序，同 priority 保持注册顺序（sort 稳定）
        self._listeners[event].sort(key=lambda item: item.priority, reverse=True)

    def emit(self, event: str, **data: Any) -> None:
        """广播通知：所有监听者按序执行，返回值被忽略。

        Args:
            event: 事件名
            data: 事件 payload（关键字参数，如 name=..., step=...）
        """
        for listener in self._listeners.get(event, []):
            listener.handler(**data)

    def waterfall(self, event: str, data: Any, **extra: Any) -> Any:
        """瀑布改写：data 依次经过每个监听者，返回值作为下一个的输入。

        Args:
            event: 事件名
            data: 待改写的数据（如消息历史列表）
            extra: 只读上下文（turn/step 等，监听者不应修改）

        Returns:
            最终改写后的数据；无监听者时原样返回
        """
        for listener in self._listeners.get(event, []):
            data = listener.handler(data, **extra)
        return data

    def bail(self, event: str, **data: Any) -> Any:
        """短路决策：第一个返回非 None 的监听者决定结果。

        Args:
            event: 事件名
            data: 决策上下文（如工具名/参数）

        Returns:
            第一个非 None 的监听者返回值；全部返回 None 时为 None
        """
        for listener in self._listeners.get(event, []):
            result = listener.handler(**data)
            if result is not None:
                return result
        return None


class _Listener:
    """内部结构：一条监听器记录（handler + priority）。"""

    __slots__ = ("handler", "priority")

    def __init__(self, handler: Callable, priority: int) -> None:
        self.handler = handler
        self.priority = priority
