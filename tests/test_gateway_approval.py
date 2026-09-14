"""网关审批桥测试（方案 2026-09-13-Web权限审批弹框方案 §3.4）。

覆盖三块：
1. **契约**：通知字段（question/options/name/code/command 脱敏/root_session_id/
   timeout_ms）与 `approval/respond` 的 choice 往返（含旧 decision 兼容）。
2. **缺陷修复**：D1 审批 id 唯一（不再 len+1 复用）；D2 响应校验会话归属 +
   choice 必须在该审批的选项集内；D3 通知带档位与工具名。
3. **fail-closed**：超时 → None（拒绝）；重复响应幂等；未知 id 明确报错。
"""

import json
import threading
import time
import unittest.mock as mock

import pytest

from qi_agent.gateway.gateway import Gateway
from qi_agent.gateway.protocol import RpcError


class _FastClient:
    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant", "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def _make_gateway() -> Gateway:
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient", lambda key: _FastClient()).start()
    return Gateway()


def _collect(gw: Gateway) -> list[dict]:
    notifications: list[dict] = []
    gw.shell_callback = lambda json_str: notifications.append(json.loads(json_str))
    return notifications


def _approval_notice(notifications: list[dict]) -> dict:
    return next(n for n in notifications if n["method"] == "serverRequest/approval")


def _start_request(gw: Gateway, session_id: str, **kwargs) -> tuple[dict, threading.Thread]:
    """后台线程发起审批请求（模拟被阻塞的 agent 线程）。"""
    box: dict = {}
    thread = threading.Thread(
        target=lambda: box.update(choice=gw.request_approval(session_id, **kwargs)))
    thread.start()
    return box, thread


def _wait_for_notice(notifications: list[dict], timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return _approval_notice(notifications)
        except StopIteration:
            time.sleep(0.02)
    raise AssertionError(f"未收到审批通知：{notifications}")


OPTIONS = [
    {"value": "once", "label": "允许一次"},
    {"value": "session", "label": "本会话总是允许"},
    {"value": "deny", "label": "拒绝"},
]


# ── 契约：通知字段 + 往返 ─────────────────────────────────────────────────


def test_approval_notice_payload() -> None:
    """通知带完整决策上下文（前端渲染依据）——含 D3 修复的 name/code。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(
        gw, session_id,
        question="执行命令 'git push'？",
        options=OPTIONS,
        meta={"tool": "shell", "code": "SEC_APPROVAL_GENERAL", "command": "git push",
              "arguments": {"command": "git push"}},
    )
    notice = _wait_for_notice(notifications)
    params = notice["params"]
    assert params["session_id"] == session_id
    assert params["root_session_id"] == session_id  # 根会话=自己
    assert params["question"] == "执行命令 'git push'？"
    assert [o["value"] for o in params["options"]] == ["once", "session", "deny"]
    assert params["name"] == "shell"
    assert params["code"] == "SEC_APPROVAL_GENERAL"
    assert params["timeout_ms"] == 60000
    gw._respond_approval(session_id, params["approval_id"], choice="once")
    thread.join(timeout=5)
    assert box["choice"] == "once"


def test_approval_command_redacted() -> None:
    """命令展示前在内核侧脱敏（密钥不出进程）。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    secret = "sk-live-abcdef1234567890"
    box, thread = _start_request(
        gw, session_id, question="执行？", options=OPTIONS,
        meta={"tool": "shell", "command": f"curl -H 'Authorization: Bearer {secret}' x"},
    )
    params = _wait_for_notice(notifications)["params"]
    assert secret not in params["command"]
    assert "Bearer" in params["command"] or "curl" in params["command"]
    gw._respond_approval(session_id, params["approval_id"], choice="deny")
    thread.join(timeout=5)


def test_legacy_decision_param_accepted() -> None:
    """旧前端（approve/deny）兼容映射：approve→once，deny→deny。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(gw, session_id, question="执行？", options=OPTIONS)
    params = _wait_for_notice(notifications)["params"]
    gw._respond_approval(session_id, params["approval_id"], decision="approve")
    thread.join(timeout=5)
    assert box["choice"] == "once"


# ── D1：审批 id 唯一 ──────────────────────────────────────────────────────


def test_approval_ids_unique() -> None:
    """连续审批不复用 id（旧实现 f"ap_{len+1}" 会重用 ap_1）。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    seen: set[str] = set()
    for _ in range(12):
        notifications = _collect(gw)
        box, thread = _start_request(gw, session_id, question="执行？", options=OPTIONS)
        params = _wait_for_notice(notifications)["params"]
        seen.add(params["approval_id"])
        gw._respond_approval(session_id, params["approval_id"], choice="deny")
        thread.join(timeout=5)
    assert len(seen) == 12


# ── D2：会话归属 + 选项集校验 ─────────────────────────────────────────────


def test_respond_rejects_foreign_session() -> None:
    """别的会话拿 approval_id 来放行 → 报错且审批仍挂起（不串线）。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    other_id = gw._create_session(goal="另一个")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(gw, session_id, question="执行？", options=OPTIONS)
    params = _wait_for_notice(notifications)["params"]

    with pytest.raises(RpcError):
        gw._respond_approval(other_id, params["approval_id"], choice="once")
    assert thread.is_alive()  # 未被放行

    gw._respond_approval(session_id, params["approval_id"], choice="deny")
    thread.join(timeout=5)
    assert box["choice"] == "deny"


def test_respond_rejects_choice_outside_options() -> None:
    """沙箱档（只有 once/deny）收到 session → 拒绝该响应（不能扩大授权）。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(
        gw, session_id, question="降级沙箱？",
        options=[{"value": "once", "label": "允许一次"},
                 {"value": "deny", "label": "拒绝"}],
    )
    params = _wait_for_notice(notifications)["params"]
    with pytest.raises(RpcError):
        gw._respond_approval(session_id, params["approval_id"], choice="session")
    gw._respond_approval(session_id, params["approval_id"], choice="deny")
    thread.join(timeout=5)


def test_respond_unknown_id_errors() -> None:
    """未知/已过期 approval_id → 明确报错（前端可清理弹窗）。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    with pytest.raises(RpcError):
        gw._respond_approval(session_id, "ap_不存在", choice="once")


def test_respond_duplicate_is_idempotent() -> None:
    """重复响应 → 第二次不报错（幂等），且不改变第一次的结果。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(gw, session_id, question="执行？", options=OPTIONS)
    params = _wait_for_notice(notifications)["params"]
    gw._respond_approval(session_id, params["approval_id"], choice="deny")
    thread.join(timeout=5)
    assert box["choice"] == "deny"
    result = gw._respond_approval(session_id, params["approval_id"], choice="deny")
    assert result.get("duplicate") is True


# ── fail-closed ──────────────────────────────────────────────────────────


def test_approval_timeout_returns_none() -> None:
    """超时 → None（= 拒绝），不抛异常、不挂死。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    _collect(gw)  # 外壳收到通知但不响应
    choice = gw.request_approval(session_id, "危险命令", OPTIONS, timeout=0.1)
    assert choice is None


def test_root_session_resolution() -> None:
    """子 context 的审批归属到根会话（前端按根会话匹配弹窗）。"""
    from qi_agent.context.context import AgentContext

    gw = _make_gateway()
    root_id = gw._create_session(goal="根")["session_id"]
    child = AgentContext(persist=False)
    child.parent_id = root_id
    gw.manager.register(child, role="subagent")
    assert gw._resolve_root_session(child.id) == root_id
    assert gw._resolve_root_session(root_id) == root_id
    assert gw._resolve_root_session("ctx_不存在") == "ctx_不存在"  # 兜底原样返回


# ── M2-a：审批记录可回看（tool_call_id 绑定 + 已决通知）─────────────────


def _resolved_notice(notifications: list[dict]) -> dict:
    return next(n for n in notifications if n["method"] == "item/approvalResolved")


def test_notice_carries_tool_call_id() -> None:
    """请求通知带 tool_call_id + turn：前端据此把审批绑到"触发它的那一行工具"。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(
        gw, session_id, question="执行？", options=OPTIONS,
        meta={"tool": "shell", "code": "SEC_APPROVAL_GENERAL",
              "command": "git push", "tool_call_id": "call_7", "turn": 3},
    )
    params = _wait_for_notice(notifications)["params"]
    assert params["tool_call_id"] == "call_7"
    assert params["turn"] == 3
    gw._respond_approval(session_id, params["approval_id"], choice="deny")
    thread.join(timeout=5)
    assert box["choice"] == "deny"


def test_resolved_notice_on_decision() -> None:
    """决策后内核必须发"已决"通知（记录由内核产出，前端只渲染）。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(
        gw, session_id, question="执行？", options=OPTIONS,
        meta={"tool": "run_python", "code": "SEC_APPROVAL_SANDBOX",
              "command": "import 'os'", "tool_call_id": "call_9"},
    )
    request_params = _wait_for_notice(notifications)["params"]
    gw._respond_approval(session_id, request_params["approval_id"], choice="once")
    thread.join(timeout=5)

    params = _resolved_notice(notifications)["params"]
    assert params["approval_id"] == request_params["approval_id"]
    assert params["tool_call_id"] == "call_9"
    assert params["choice"] == "once"
    assert params["waited_ms"] >= 0
    assert params["decided_at"] > 0


def test_resolved_notice_on_timeout() -> None:
    """超时也要留记录（此前只写 run.log）→ choice="timeout"。"""
    gw = _make_gateway()
    session_id = gw._create_session(goal="测试")["session_id"]
    notifications = _collect(gw)
    box, thread = _start_request(
        gw, session_id, question="执行？", options=OPTIONS, timeout=0.2,
        meta={"tool": "shell", "code": "SEC_APPROVAL_GENERAL",
              "command": "rm -rf x", "tool_call_id": "call_t"},
    )
    thread.join(timeout=5)

    assert box["choice"] is None
    params = _resolved_notice(notifications)["params"]
    assert params["choice"] == "timeout"
    assert params["tool_call_id"] == "call_t"
