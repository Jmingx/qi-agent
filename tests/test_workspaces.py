from pathlib import Path

import pytest

from qi_agent.context.context import AgentContext
from qi_agent.workspaces import SessionWorkspace


def test_context_holds_session_workspace(tmp_path: Path) -> None:
    workspace = SessionWorkspace("ws_demo", tmp_path.resolve(), "demo")
    context = AgentContext(workspace=workspace)
    assert context.workspace is workspace


def test_workspace_blocks_parent_escape(tmp_path: Path) -> None:
    workspace = SessionWorkspace("ws_demo", tmp_path.resolve(), "demo")
    assert workspace.resolve_path("src/main.py") == tmp_path.resolve() / "src" / "main.py"
    with pytest.raises(ValueError, match="越出"):
        workspace.resolve_path("../outside.txt")
