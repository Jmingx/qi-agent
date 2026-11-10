"""工作空间失效与路径校验的网关回归测试。"""

from __future__ import annotations

import json
import unittest.mock as mock

from qi_agent.gateway.gateway import Gateway
from qi_agent.gateway.protocol import ERROR_INVALID_PARAMS
from qi_agent.storage.sqlite_store import SQLiteStore


class _FastClient:
    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(
            content="ok", tool_calls=[],
            assistant_message={"role": "assistant", "content": "ok"}, usage=None,
        )

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def _gateway(store: SQLiteStore) -> Gateway:
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient", lambda key: _FastClient()).start()
    return Gateway(storage=store)


def _dispatch(gateway: Gateway, method: str, params: dict) -> dict:
    return json.loads(gateway.dispatcher.dispatch(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    })))


def test_workspace_add_reports_clean_invalid_path_errors(tmp_path) -> None:
    gateway = _gateway(SQLiteStore(db_path=str(tmp_path / "workspace.db")))
    missing = _dispatch(gateway, "workspace/add", {"path": str(tmp_path / "missing")})
    relative = _dispatch(gateway, "workspace/add", {"path": "relative"})
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    file_result = _dispatch(gateway, "workspace/add", {"path": str(file_path)})

    assert missing["error"] == {
        "code": ERROR_INVALID_PARAMS,
        "message": f"目录不存在：{tmp_path / 'missing'}",
    }
    assert relative["error"] == {"code": ERROR_INVALID_PARAMS, "message": "工作空间必须是绝对路径"}
    assert file_result["error"] == {"code": ERROR_INVALID_PARAMS, "message": "工作空间必须是目录"}
    assert "WinError" not in missing["error"]["message"]
    assert "{" not in missing["error"]["message"]


def test_removed_workspace_degrades_for_create_and_resume(tmp_path) -> None:
    store = SQLiteStore(db_path=str(tmp_path / "workspace.db"))
    root = tmp_path / "project"
    root.mkdir()
    store.add_workspace("ws_removed", "project", str(root))
    gateway = _gateway(store)
    session = gateway._create_session(workspace_id="ws_removed")
    gateway._send_message(session["session_id"], "保存会话")
    assert gateway._remove_workspace("ws_removed") == {"removed": True}

    created = gateway._create_session(workspace_id="ws_removed")
    assert created["workspace_id"] is None
    assert created["workspace_missing"] is True

    gateway.manager.unregister(session["session_id"])
    resumed = gateway._resume_session(session["session_id"])
    assert resumed["messages"] > 0
    assert resumed["workspace_available"] is False
    assert resumed["workspace_missing"] is True


def test_missing_workspace_remove_is_explicit_invalid_params(tmp_path) -> None:
    gateway = _gateway(SQLiteStore(db_path=str(tmp_path / "workspace.db")))
    response = _dispatch(gateway, "workspace/remove", {"workspace_id": "ws_missing"})
    assert response["error"] == {
        "code": ERROR_INVALID_PARAMS,
        "message": "工作空间不存在或已移除",
    }
