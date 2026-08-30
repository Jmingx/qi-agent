"""CLI 外壳命令测试（方案 2026-08-28 内核外壳分离——CliShell 行为）。

验证外壳命令：/status 状态、/stop 中断、/new 新会话、/resume 恢复、
/remember 记忆、clear 清空——全部走 Gateway（不碰内核）。
"""

import unittest.mock as mock

from qi_agent.cli import CliShell


def _make_shell(gw_attrs: dict | None = None) -> CliShell:
    """构造 CliShell + 假 Gateway（方法全 mock）。"""
    gw = mock.MagicMock()
    gw.session_id = "ctx_main"
    gw._create_session.return_value = {"session_id": "ctx_main"}
    gw._send_message.return_value = {"reply": "ok"}
    gw._stop_session.return_value = {"stopped": True}
    gw._resume_session.return_value = {
        "session_id": "ctx_old", "messages": 3, "turn": 1}
    if gw_attrs:
        for k, v in gw_attrs.items():
            setattr(gw, k, v)
    shell = CliShell(gateway=gw)
    shell.session_id = "ctx_main"
    return shell


def test_new_command_starts_session() -> None:
    """/new → 新会话（_create_session 被调）。"""
    shell = _make_shell()
    assert shell._handle_command("/new") is True
    assert shell.session_id == "ctx_main"


def test_status_command_prints(capsys) -> None:
    """/status → 打印状态（从 Gateway manager 读 context）。"""
    ctx = mock.MagicMock()
    ctx.turn = 3
    ctx.messages = [1, 2, 3]
    ctx.status.value = "completed"
    ctx.phase.value = "done"
    ctx.usage = {"prompt_tokens": 10, "completion_tokens": 5,
                 "total_tokens": 15}
    gw = mock.MagicMock()
    gw.manager.contexts = {"ctx_main": ctx}
    shell = CliShell(gateway=gw)
    shell.session_id = "ctx_main"
    assert shell._handle_command("/status") is True
    out = capsys.readouterr().out
    assert "轮数: 3" in out
    assert "total: 15" in out


def test_stop_command_calls_gateway() -> None:
    """/stop → 调 Gateway._stop_session。"""
    shell = _make_shell()
    assert shell._handle_command("/stop") is True
    shell.gateway._stop_session.assert_called()


def test_remember_command_writes_memory(tmp_path) -> None:
    """/remember → sticky + MEMORY.md（跨会话）。"""
    import qi_agent.storage.memory_store as ms
    from qi_agent.context.sticky import get_sticky_text

    ms._DEFAULT_DIR = str(tmp_path)
    shell = _make_shell()
    assert shell._handle_command("/remember 用户叫王五") is True
    assert "用户叫王五" in get_sticky_text()
    assert any("用户叫王五" in e
               for e in ms.MemoryStore().list_entries("memory"))


def test_clear_command_starts_new_session() -> None:
    """clear → 新会话（_create_session 被调）。"""
    shell = _make_shell()
    assert shell._handle_command("/clear") is True
    shell.gateway._create_session.assert_called()


def test_exit_command_returns_true() -> None:
    """exit → 返回 True（主循环退出）。"""
    shell = _make_shell()
    assert shell._handle_command("/exit") is True


def test_help_command_prints(capsys) -> None:
    """/help → 打印命令及用途（HELP_TEXT）。"""
    shell = _make_shell()
    assert shell._handle_command("/help") is True
    out = capsys.readouterr().out
    assert "/exit" in out
    assert "/remember" in out
    assert "/delegate" in out
    assert "退出" in out  # 含用途说明


def test_delegate_command_spawns(capsys) -> None:
    """/delegate <目标> → 拉起子 agent（2026-08-30 补全：之前空壳）。"""
    import unittest.mock as mock
    from qi_agent.cli import CliShell

    gw = mock.MagicMock()
    gw.manager.contexts = {"ctx_main": mock.MagicMock()}
    gw._create_session.return_value = {"session_id": "ctx_main"}
    gw._delegate.return_value = {"session_id": "agt_sub", "status": "spawned"}
    shell = CliShell(gateway=gw)
    assert shell._handle_command("/delegate 查一下天气") is True
    out = capsys.readouterr().out
    assert "agt_sub" in out  # 显示子 agent id
    gw._delegate.assert_called_once()


def test_normal_message_sends_to_gateway() -> None:
    """非命令 → 返回 False（主循环当对话消息发 Gateway）。"""
    shell = _make_shell()
    assert shell._handle_command("你好") is False


def test_approval_notification_renders(capsys) -> None:
    """审批通知 → 弹窗（外壳读 stdin 唯一交互点）。"""
    import json

    shell = _make_shell()
    shell.session_id = "ctx_main"
    # mock input（用户批准）
    with mock.patch("builtins.input", return_value="1"):
        shell._on_notification(json.dumps({
            "jsonrpc": "2.0", "method": "serverRequest/approval",
            "params": {"approval_id": "ap_1", "command": "patch 编辑 X",
                       "session_id": "ctx_main"}}))
    out = capsys.readouterr().out
    assert "审批" in out
    # 响应回网关（_respond_approval 被调）
    shell.gateway._respond_approval.assert_called()
