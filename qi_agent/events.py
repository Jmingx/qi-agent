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

from qi_agent.logging_setup import get_events_logger


def _changed(before: Any, after: Any) -> str:
    """记录瀑布改写的变化（before → after 摘要——完整内容不省略，
    但消息列表只显示长度变化 + 新增部分，避免刷屏）。"""
    if before is after:
        return "(unchanged)"
    # 消息列表（瀑布主场景）：显示长度变化 + 新增消息内容
    if isinstance(before, list) and isinstance(after, list):
        added = after[len(before):]
        removed = len(before) - len(after)
        desc = f"len {len(before)}→{len(after)}"
        if added:
            desc += f" +added={added}"
        if removed > 0:
            desc += f" -removed={removed}"
        return desc
    return f"{before!r} -> {after!r}"


class EventBus:
    """事件总线：插件注册监听器，主循环在事件点分发。"""

    def __init__(self, context_id: str = "") -> None:
        self._listeners: dict[str, list[_Listener]] = {}
        self.context_id = context_id  # 归属 context（2026-08-30：日志定位用）

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
        # 日志（events.log——监听注册审计；完整打印 + context_id 定位）
        get_events_logger().info(
            "on context=%s event=%s handler=%s priority=%d",
            self.context_id, event,
            getattr(handler, "__name__", str(handler)), priority)

    def emit(self, event: str, **data: Any) -> None:
        """广播通知：所有监听者按序执行，返回值被忽略。

        Args:
            event: 事件名
            data: 事件 payload（关键字参数，如 name=..., step=...）
        """
        listeners = self._listeners.get(event, [])
        logger = get_events_logger()
        # 日志（events.log——事件分发审计；完整打印 data + 每个监听者
        # 执行明细：谁响应了、执行了什么。2026-08-30 用户要求）
        logger.info(
            "emit context=%s event=%s listeners=%d data=%s",
            self.context_id, event, len(listeners), data)
        for listener in listeners:
            name = getattr(listener.handler, "__name__", str(listener.handler))
            try:
                ret = listener.handler(**data)
                logger.info(
                    "  run context=%s event=%s handler=%s -> %s",
                    self.context_id, event, name, ret)
            except Exception as exc:
                logger.error(
                    "  run context=%s event=%s handler=%s EXC=%s",
                    self.context_id, event, name, exc)
                raise

    def waterfall(self, event: str, data: Any, **extra: Any) -> Any:
        """瀑布改写：data 依次经过每个监听者，返回值作为下一个的输入。

        Args:
            event: 事件名
            data: 待改写的数据（如消息历史列表）
            extra: 只读上下文（turn/step 等，监听者不应修改）

        Returns:
            最终改写后的数据；无监听者时原样返回
        """
        listeners = self._listeners.get(event, [])
        logger = get_events_logger()
        logger.info(
            "waterfall context=%s event=%s listeners=%d data=%s",
            self.context_id, event, len(listeners), data)
        for listener in listeners:
            name = getattr(listener.handler, "__name__", str(listener.handler))
            before = data
            try:
                data = listener.handler(data, **extra)
                logger.info(
                    "  run context=%s event=%s handler=%s -> %s",
                    self.context_id, event, name,
                    _changed(before, data))
            except Exception as exc:
                logger.error(
                    "  run context=%s event=%s handler=%s EXC=%s",
                    self.context_id, event, name, exc)
                raise
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
