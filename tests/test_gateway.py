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


def test_create_session_preserves_optional_metadata() -> None:
    """session/create 的评测元数据只附着 Context，不进入 goal。"""
    gw = _make_gateway()
    sess = gw._create_session(
        goal="测试",
        metadata={"eval_case_id": "case-1", "eval_run_id": "run-1"},
    )
    context = gw.manager.get_context(sess["session_id"])
    assert context is not None
    assert context.metadata == {
        "eval_case_id": "case-1",
        "eval_run_id": "run-1",
    }
    assert context.goal == "测试"

    unsafe = gw._create_session(metadata={"prompt": "不要进入 metadata"})
    unsafe_context = gw.manager.get_context(unsafe["session_id"])
    assert unsafe_context is not None
    assert unsafe_context.metadata == {}


def test_pick_workspace_uses_native_folder_dialog(tmp_path) -> None:
    """目录选择由 Gateway 发起，浏览器不需要也不能提供绝对路径。"""
    gw = _make_gateway()
    dialog = mock.Mock()
    with mock.patch("tkinter.Tk", return_value=dialog), mock.patch(
        "tkinter.filedialog.askdirectory", return_value=str(tmp_path)
    ) as askdirectory:
        result = gw._pick_workspace()

    assert result["selected"] is True
    assert result["path"] == str(tmp_path.resolve())
    assert result["label"] == tmp_path.name
    askdirectory.assert_called_once_with(
        parent=dialog,
        title="选择 qi-agent 工作空间",
        mustexist=True,
    )
    dialog.withdraw.assert_called_once()
    dialog.destroy.assert_called_once()


def test_pick_workspace_allows_user_cancel() -> None:
    """用户取消系统对话框不应产生工作空间登记。"""
    gw = _make_gateway()
    dialog = mock.Mock()
    with mock.patch("tkinter.Tk", return_value=dialog), mock.patch(
        "tkinter.filedialog.askdirectory", return_value=""
    ):
        assert gw._pick_workspace() == {"selected": False}


def test_send_unknown_session_error() -> None:
    """未知会话 → 错误码 -32001（会话不存在）。"""
    gw = _make_gateway()
    raw = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"session_id": "ctx_nope", "text": "你好"}})
    resp = json.loads(gw.dispatcher.dispatch(raw))
    assert resp["error"]["code"] == -32001


def test_approval_flow() -> None:
    """审批请求-响应桥：request_approval 阻塞 → respond 唤醒（choice 语义）。"""
    gw = _make_gateway()
    sess = gw._create_session()

    notifications = []
    gw.shell_callback = lambda json_str: notifications.append(
        json.loads(json_str))

    # 后台线程请求审批（模拟内核工具执行）
    result_box = {}
    t = threading.Thread(
        target=lambda: result_box.update(
            r=gw.request_approval(
                sess["session_id"], "执行命令 'git push'？",
                [{"value": "once", "label": "允许一次"},
                 {"value": "deny", "label": "拒绝"}],
                meta={"tool": "shell", "code": "SEC_APPROVAL_GENERAL",
                      "command": "git push origin main"},
            )))
    t.start()
    time.sleep(0.2)  # 等审批请求发出

    # 外壳收到 approval 通知
    assert any(n["method"] == "serverRequest/approval"
               for n in notifications), f"通知: {notifications}"
    approval = next(n for n in notifications
                    if n["method"] == "serverRequest/approval")
    approval_id = approval["params"]["approval_id"]

    # 外壳响应批准
    gw._respond_approval(sess["session_id"], approval_id, choice="once")
    t.join(timeout=5)
    assert result_box.get("r") == "once"  # 批准 → 选项值


def test_approval_timeout_denies() -> None:
    """审批超时 → None（拒绝，fail-closed）。"""
    gw = _make_gateway()
    sess = gw._create_session()
    gw.shell_callback = lambda json_str: None  # 外壳不响应

    result = gw.request_approval(
        sess["session_id"], "危险命令",
        [{"value": "once", "label": "允许一次"}, {"value": "deny", "label": "拒绝"}],
        timeout=0.1,
    )
    assert result is None  # 超时 = 拒绝


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
