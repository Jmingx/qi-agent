"""网关 JSON-RPC 协议测试（方案 2026-08-28-内核外壳分离）。

验证：dispatch 解析/方法分发/错误码/通知（无 id）/审批请求-响应桥。
"""

import json

import pytest

from qi_agent.gateway.protocol import (
    ERROR_INTERNAL, ERROR_INVALID_PARAMS, ERROR_INVALID_REQUEST,
    ERROR_METHOD_NOT_FOUND, ERROR_PARSE, ERROR_SESSION_NOT_FOUND,
    ERROR_CONCURRENT_RUN,
    RpcRequest, RpcResponse, RpcNotification, parse_message,
)


def test_parse_request() -> None:
    """解析请求（带 id）。"""
    msg = parse_message(
        '{"jsonrpc":"2.0","id":1,"method":"message/send",'
        '"params":{"session_id":"ctx_1","text":"你好"}}')
    assert isinstance(msg, RpcRequest)
    assert msg.id == 1
    assert msg.method == "message/send"
    assert msg.params == {"session_id": "ctx_1", "text": "你好"}


def test_parse_notification() -> None:
    """解析通知（无 id——不需要响应）。"""
    msg = parse_message(
        '{"jsonrpc":"2.0","method":"turn/end",'
        '"params":{"session_id":"ctx_1","status":"completed"}}')
    assert isinstance(msg, RpcNotification)
    assert msg.method == "turn/end"


def test_parse_invalid_json() -> None:
    """非法 JSON → 解析错误（-32700）。"""
    with pytest.raises(Exception) as exc:
        parse_message("not json{{{")
    assert "32700" in str(exc)


def test_response_ok() -> None:
    """成功响应格式。"""
    resp = RpcResponse(id=1, result={"session_id": "ctx_1"})
    assert json.loads(resp.to_json()) == {
        "jsonrpc": "2.0", "id": 1, "result": {"session_id": "ctx_1"}}


def test_response_error() -> None:
    """错误响应格式（带错误码）。"""
    resp = RpcResponse(id=1, error={"code": -32002,
                                    "message": "context 正在运行"})
    parsed = json.loads(resp.to_json())
    assert parsed["error"]["code"] == -32002


def test_error_codes() -> None:
    """错误码常量（JSON-RPC 标准 + 自定义）。"""
    assert ERROR_PARSE == -32700
    assert ERROR_INVALID_REQUEST == -32600
    assert ERROR_METHOD_NOT_FOUND == -32601
    assert ERROR_INVALID_PARAMS == -32602
    assert ERROR_INTERNAL == -32603
    assert ERROR_SESSION_NOT_FOUND == -32001
    assert ERROR_CONCURRENT_RUN == -32002


def test_log_rpc_decorator(tmp_path) -> None:
    """log_rpc 装饰器：写本地日志文件（方法名/参数/结果/耗时）。"""
    import qi_agent.gateway.protocol as proto
    import logging

    # 清空 logger（防测试间 handler 复用旧路径）
    logging.getLogger("qi_agent.rpc").handlers.clear()
    proto._RPC_LOG_DIR = str(tmp_path)
    from qi_agent.gateway.protocol import log_rpc

    @log_rpc("session/create")
    def handler(goal: str = "") -> dict:
        return {"session_id": "ctx_1"}

    result = handler(goal="测试")
    assert result == {"session_id": "ctx_1"}
    # 日志写入文件（~/.qi-agent/logs/rpc.log）
    log_path = tmp_path / "rpc.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "[RPC]" in content
    assert "session/create" in content
    assert "ctx_1" in content


def test_log_rpc_error(tmp_path) -> None:
    """log_rpc 装饰器：异常也记录（不吞）。"""
    import qi_agent.gateway.protocol as proto
    import logging

    logging.getLogger("qi_agent.rpc").handlers.clear()
    proto._RPC_LOG_DIR = str(tmp_path)
    from qi_agent.gateway.protocol import RpcError, log_rpc

    @log_rpc("message/send")
    def handler(**kw) -> dict:
        raise RpcError(ERROR_SESSION_NOT_FOUND, "会话不存在")

    with pytest.raises(RpcError):
        handler(session_id="ctx_nope", text="你好")
    log_path = tmp_path / "rpc.log"
    content = log_path.read_text(encoding="utf-8")
    assert "[RPC]" in content
    assert "会话不存在" in content
