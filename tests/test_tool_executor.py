"""ToolExecutor 执行闭环测试（方案 2026-08-23）。

覆盖：审批路由（同意/拒绝/fail-closed）、WARN/BLOCK 档、并发执行、
失败聚合、事件发出（tool/start、agent/tool-result）。
"""

import json
import time

from qi_agent.events import EventBus
from qi_agent.llm import ToolCall
from qi_agent.tools.decision import ToolAction, ToolDecision
from qi_agent.tools.executor import ToolExecutor
from qi_agent.tools.registry import register

_ECHO_NAME = "test_executor_echo"
_SLOW_NAME = "test_executor_slow"


def _ensure_tools() -> None:
    """注册测试工具（幂等：重复注册防护下跳过已存在）。"""
    try:
        register(
            name=_ECHO_NAME,
            handler=lambda **kw: f"echo:{json.dumps(kw, ensure_ascii=False)}",
            schema=_ECHO_SCHEMA,
        )
    except ValueError:
        pass
    try:
        register(
            name=_SLOW_NAME,
            handler=lambda delay: time.sleep(delay) or "slow-done",
            schema={
                "type": "function",
                "function": {
                    "name": _SLOW_NAME,
                    "description": "慢工具",
                    "parameters": {
                        "type": "object",
                        "properties": {"delay": {"type": "number"}},
                        "required": ["delay"],
                    },
                },
            },
        )
    except ValueError:
        pass


# echo 工具手写 schema（**kw 无法自动生成——inspect 把 **kwargs 当必填参数；
# 声明测试用参数名——approved 走 internal 跳过校验）
_ECHO_SCHEMA = {
    "type": "function",
    "function": {
        "name": _ECHO_NAME,
        "description": "回显工具",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "w": {"type": "integer"},
                "e": {"type": "integer"},
            },
        },
    },
}


def _call(cid: str, name: str, args: dict | None = None) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args or {})


def _decision(action: ToolAction, code: str = "", command: str = "",
              reason: str = "") -> ToolDecision:
    return ToolDecision(action=action, code=code, command=command, reason=reason)


class TestApprovalRouting:
    """审批分发：NEED_APPROVAL / ESCALATION → agent/tool-approval bail。"""

    def test_approved_executes(self) -> None:
        _ensure_tools()
        events = EventBus()
        events.on(
            "agent/tool-approval",
            lambda **kw: True,  # 审批同意
        )
        ex = ToolExecutor(events)
        calls = [_call("c1", _ECHO_NAME, {"x": 1})]
        decisions = {"c1": _decision(ToolAction.NEED_APPROVAL, command="echo x=1")}

        outcomes = ex.execute(calls, decisions, turn=1, step=0)

        output, _ = outcomes["c1"]
        assert "echo:" in output  # 执行了
        assert "approved" in json.loads(output.split("echo:")[1])  # 注入透传

    def test_denied_blocks_with_approval_text(self) -> None:
        _ensure_tools()
        events = EventBus()
        events.on("agent/tool-approval", lambda **kw: False)  # 拒绝
        ex = ToolExecutor(events)
        calls = [_call("c1", _ECHO_NAME)]
        decisions = {"c1": _decision(
            ToolAction.NEED_APPROVAL, command="删除文件 x")}

        output, _ = ex.execute(calls, decisions, turn=1, step=0)["c1"]

        assert output.startswith("[审批拒绝]")
        assert "删除文件 x" in output  # 拒绝原因带命令

    def test_no_approval_listener_fail_closed(self) -> None:
        """无审批监听器 → 拒绝（fail-closed，安全底线）。"""
        _ensure_tools()
        ex = ToolExecutor(EventBus())  # 空总线，无监听器
        calls = [_call("c1", _ECHO_NAME)]
        decisions = {"c1": _decision(ToolAction.NEED_APPROVAL)}

        output, _ = ex.execute(calls, decisions, turn=1, step=0)["c1"]

        assert output.startswith("[审批拒绝]")

    def test_escalation_approved_executes(self) -> None:
        _ensure_tools()
        events = EventBus()
        events.on("agent/tool-approval", lambda **kw: True)
        ex = ToolExecutor(events)
        calls = [_call("c1", _ECHO_NAME, {"x": 2})]
        decisions = {"c1": _decision(ToolAction.ESCALATION, command="执行代码")}

        output, _ = ex.execute(calls, decisions, turn=1, step=0)["c1"]

        assert "echo:" in output  # ESCALATION 档同意后照常执行

    def test_partial_denial_only_blocks_itself(self) -> None:
        """部分拒绝：一条被拒不影响其他调用（per-call 独立判定）。

        同一批 3 个调用：c1 拒绝、c2 同意、c3 放行——
        c1 出 [审批拒绝]，c2/c3 照常并发执行。
        """
        _ensure_tools()
        events = EventBus()

        def fake_approval(name: str, **_) -> bool | None:
            # 按工具名逐条裁决：echo 同意，另一个工具拒绝
            return name != _SLOW_NAME

        events.on("agent/tool-approval", fake_approval)
        ex = ToolExecutor(events)
        calls = [
            _call("c1", _SLOW_NAME, {"delay": 0.05}),   # 拒绝
            _call("c2", _ECHO_NAME, {"x": 1}),          # 同意
            _call("c3", _ECHO_NAME, {"x": 2}),          # 放行（无审批档）
        ]
        decisions = {
            "c1": _decision(ToolAction.NEED_APPROVAL, command="慢工具"),
            "c2": _decision(ToolAction.NEED_APPROVAL, command="回显"),
            "c3": None,  # 本就不需审批
        }

        outcomes = ex.execute(calls, decisions, turn=1, step=0)

        # c1 拒绝：只它自己被拦截
        assert outcomes["c1"][0].startswith("[审批拒绝]")
        # c2 同意 + c3 放行：都执行了
        assert "echo:" in outcomes["c2"][0]
        assert "echo:" in outcomes["c3"][0]
        assert "slow-done" not in outcomes["c1"][0]  # 拒绝的没执行


class TestBlockAndWarn:
    """BLOCK（硬拒）与 WARN（警告放行）档。"""

    def test_block_returns_safety_text(self) -> None:
        _ensure_tools()
        ex = ToolExecutor(EventBus())
        calls = [_call("c1", _ECHO_NAME)]
        decisions = {"c1": _decision(
            ToolAction.BLOCK, code="SEC_BLOCK_REDLINE", reason="危险操作")}

        output, _ = ex.execute(calls, decisions, turn=1, step=0)["c1"]

        assert output == "[安全拦截] 危险操作"

    def test_approval_denied_code_maps_to_approval_text(self) -> None:
        """SEC_APPROVAL_DENIED（审批拒绝）→ [审批拒绝] 文本（行为不变）。"""
        _ensure_tools()
        ex = ToolExecutor(EventBus())
        calls = [_call("c1", _ECHO_NAME)]
        decisions = {"c1": _decision(
            ToolAction.BLOCK, code="SEC_APPROVAL_DENIED", command="rm -rf")}

        output, _ = ex.execute(calls, decisions, turn=1, step=0)["c1"]

        assert output.startswith("[审批拒绝]")

    def test_warn_executes_with_suffix(self) -> None:
        _ensure_tools()
        ex = ToolExecutor(EventBus())
        calls = [_call("c1", _ECHO_NAME, {"w": 1})]
        decisions = {"c1": _decision(ToolAction.WARN, reason="注意！")}

        output, _ = ex.execute(calls, decisions, turn=1, step=0)["c1"]

        assert output.endswith("\n[警告] 注意！")  # 执行 + 警告后缀
        assert "echo:" in output


class TestConcurrency:
    """并发执行：多 call 并行 + 失败聚合。"""

    def test_parallel_execution(self) -> None:
        _ensure_tools()
        ex = ToolExecutor(EventBus())
        # 3 个慢工具并行：串行 3s → 并行 ~1s
        calls = [_call(f"c{i}", _SLOW_NAME, {"delay": 0.8}) for i in range(3)]
        decisions = {c.id: None for c in calls}

        start = time.perf_counter()
        outcomes = ex.execute(calls, decisions, turn=1, step=0)
        elapsed = time.perf_counter() - start

        assert all(outcomes[c.id][0] == "slow-done" for c in calls)
        assert elapsed < 2.0  # 并行（远小于串行 2.4s）

    def test_failure_aggregation(self) -> None:
        """单工具失败不中断整体：错误消息回填，其他正常执行。"""
        _ensure_tools()
        ex = ToolExecutor(EventBus())
        # 用未知工具模拟失败（execute_tool 返回错误字符串不抛异常）
        calls = [_call("c1", "no_such_tool_xyz"), _call("c2", _ECHO_NAME)]
        decisions = {"c1": None, "c2": None}

        outcomes = ex.execute(calls, decisions, turn=1, step=0)

        assert "[工具错误]" in outcomes["c1"][0]
        assert "echo:" in outcomes["c2"][0]


class TestEvents:
    """执行生命周期事件：tool/start、agent/tool-result（D4 订阅点不变）。"""

    def test_start_and_result_events(self) -> None:
        _ensure_tools()
        events = EventBus()
        started: list[str] = []
        results: list[str] = []
        events.on("tool/start", lambda **kw: started.append(kw["name"]))
        events.on("agent/tool-result", lambda **kw: results.append(kw["output"]))
        ex = ToolExecutor(events)
        calls = [_call("c1", _ECHO_NAME, {"e": 1})]
        decisions = {"c1": None}

        ex.execute(calls, decisions, turn=1, step=0)

        assert started == [_ECHO_NAME]
        assert results and "echo:" in results[0]

    def test_blocked_call_emits_result_event(self) -> None:
        """被拦截的调用也发 tool-result（观测：拦截也是结果）。"""
        _ensure_tools()
        events = EventBus()
        results: list[tuple[str, str]] = []
        events.on(
            "agent/tool-result",
            lambda **kw: results.append((kw["name"], kw["output"])),
        )
        ex = ToolExecutor(events)
        calls = [_call("c1", _ECHO_NAME)]
        decisions = {"c1": _decision(
            ToolAction.BLOCK, code="SEC_BLOCK_BLACKLIST", reason="黑名单")}

        ex.execute(calls, decisions, turn=1, step=0)

        assert results == [(_ECHO_NAME, "[安全拦截] 黑名单")]
