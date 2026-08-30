"""网关集成测试（方案 2026-08-28——Gateway 方法 + 审批桥）。

验证：session/create → message/send → approval 请求-响应桥 →
     流式通知 → 并发拒绝（RUNNING 错误码）。
"""

import json
import threading
import time
import unittest.mock as mock


from qi_agent.gateway.gateway import Gateway


class _FastClient:
    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def _make_gateway():
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient",
                      lambda key: _FastClient()).start()
    return Gateway()


def test_create_and_send() -> None:
    """session/create → message/send 全链路。"""
    gw = _make_gateway()
    sess = gw._create_session(goal="测试")
    assert sess["session_id"].startswith("ctx_")
    # 通过 dispatch（协议层）
    raw = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"session_id": sess["session_id"], "text": "你好"}})
    resp = json.loads(gw.dispatcher.dispatch(raw))
    assert resp["id"] == 1
    assert "reply" in resp["result"]


def test_send_unknown_session_error() -> None:
    """未知会话 → 错误码 -32001（会话不存在）。"""
    gw = _make_gateway()
    raw = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"session_id": "ctx_nope", "text": "你好"}})
    resp = json.loads(gw.dispatcher.dispatch(raw))
    assert resp["error"]["code"] == -32001


def test_approval_flow() -> None:
    """审批请求-响应桥：request_approval 阻塞 → respond 唤醒。"""
    gw = _make_gateway()
    sess = gw._create_session()

    notifications = []
    gw.shell_callback = lambda json_str: notifications.append(
        json.loads(json_str))

    # 后台线程请求审批（模拟内核工具执行）
    result_box = {}
    t = threading.Thread(
        target=lambda: result_box.update(
            r=gw.request_approval(sess["session_id"], "patch 编辑 X")))
    t.start()
    time.sleep(0.2)  # 等审批请求发出

    # 外壳收到 approval 通知
    assert any(n["method"] == "serverRequest/approval"
               for n in notifications), f"通知: {notifications}"
    approval = next(n for n in notifications
                    if n["method"] == "serverRequest/approval")
    approval_id = approval["params"]["approval_id"]

    # 外壳响应批准
    gw._respond_approval(sess["session_id"], approval_id, "approve")
    t.join(timeout=5)
    assert result_box.get("r") is True  # 批准 → True


def test_approval_timeout_denies() -> None:
    """审批超时 → 拒绝（fail-closed）。"""
    gw = _make_gateway()
    sess = gw._create_session()
    gw.shell_callback = lambda json_str: None  # 外壳不响应

    # 短超时（不真等 60s——直接测超时拒绝逻辑）
    import qi_agent.gateway.gateway as g
    orig = g.APPROVAL_TIMEOUT
    g.APPROVAL_TIMEOUT = 0.1
    try:
        result = gw.request_approval(sess["session_id"], "危险命令")
        assert result is False  # 超时 = 拒绝
    finally:
        g.APPROVAL_TIMEOUT = orig


def test_stream_notification() -> None:
    """流式输出 → item/agentMessage/delta 通知。"""
    gw = _make_gateway()
    sess = gw._create_session()
    notifications = []
    gw.shell_callback = lambda json_str: notifications.append(
        json.loads(json_str))

    # 模拟流式回调（_make_stream_callback）
    cb = gw._make_stream_callback(sess["session_id"])
    cb("你")
    cb("好")
    deltas = [n["params"]["text"] for n in notifications
              if n["method"] == "item/agentMessage/delta"]
    assert deltas == ["你", "好"]
