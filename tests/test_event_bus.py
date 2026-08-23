"""事件总线单元测试：emit（通知）/ waterfall（改写）/ bail（决策）+ 优先级。

方案：docs/plans/2026-08-18-事件化改造方案.md（决策点 1-7 已批准）
"""

from qi_agent.events import EventBus


def test_emit_calls_all_handlers() -> None:
    """emit 应按注册顺序调用所有监听者。"""
    bus = EventBus()
    calls: list[str] = []
    bus.on("test", lambda **_: calls.append("a"))
    bus.on("test", lambda **_: calls.append("b"))
    bus.emit("test")
    assert calls == ["a", "b"]


def test_emit_ignores_return_value() -> None:
    """emit 应忽略监听者返回值。"""
    bus = EventBus()
    bus.on("test", lambda **_: "ignored")
    # 不抛错即通过（emit 无返回值）
    assert bus.emit("test") is None


def test_emit_passes_data() -> None:
    """emit 应把 payload 传给监听者。"""
    bus = EventBus()
    received: dict = {}

    def handler(**data) -> None:
        received.update(data)

    bus.on("test", handler)
    bus.emit("test", name="get_time", step=1)
    assert received == {"name": "get_time", "step": 1}


def test_waterfall_chains_values() -> None:
    """waterfall 应依次改写数据，最终结果回传。"""
    bus = EventBus()
    bus.on("pre", lambda data, **_: data + "b")
    bus.on("pre", lambda data, **_: data + "c")
    assert bus.waterfall("pre", "a") == "abc"


def test_waterfall_no_listener_returns_original() -> None:
    """无监听者时 waterfall 应原样返回。"""
    bus = EventBus()
    data = {"role": "user", "content": "hi"}
    assert bus.waterfall("pre", data) is data


def test_waterfall_passes_extra() -> None:
    """waterfall 应把 extra 只读信息传给监听者。"""
    bus = EventBus()
    seen: dict = {}

    def handler(data, **extra):
        seen.update(extra)
        return data

    bus.on("pre", handler)
    bus.waterfall("pre", [], turn=1, step=2)
    assert seen == {"turn": 1, "step": 2}


def test_bail_stops_at_first_non_none() -> None:
    """bail 应在第一个非 None 返回处停止，后续监听者不执行。"""
    bus = EventBus()
    calls: list[str] = []

    def first(**_) -> str | None:
        calls.append("first")
        return "拦截"

    def second(**_) -> str | None:
        calls.append("second")
        return "不该执行"

    bus.on("decide", first)
    bus.on("decide", second)
    assert bus.bail("decide") == "拦截"
    assert calls == ["first"]


def test_bail_all_none_returns_none() -> None:
    """全部监听者返回 None 时 bail 应返回 None。"""
    bus = EventBus()
    bus.on("decide", lambda **_: None)
    bus.on("decide", lambda **_: None)
    assert bus.bail("decide") is None


def test_priority_order() -> None:
    """priority 大的监听者应先执行。"""
    bus = EventBus()
    calls: list[str] = []
    bus.on("test", lambda **_: calls.append("low"), priority=0)
    bus.on("test", lambda **_: calls.append("high"), priority=100)
    bus.emit("test")
    assert calls == ["high", "low"]


def test_priority_stable_order() -> None:
    """同 priority 应按注册顺序执行（稳定排序）。"""
    bus = EventBus()
    calls: list[str] = []
    bus.on("test", lambda **_: calls.append("first"), priority=0)
    bus.on("test", lambda **_: calls.append("second"), priority=0)
    bus.emit("test")
    assert calls == ["first", "second"]


def test_events_are_namespaced() -> None:
    """不同事件名的监听者互不影响。"""
    bus = EventBus()
    calls: list[str] = []
    bus.on("agent/tool-call", lambda **_: calls.append("tool"))
    bus.on("agent/final-answer", lambda **_: calls.append("final"))
    bus.emit("agent/final-answer")
    assert calls == ["final"]
