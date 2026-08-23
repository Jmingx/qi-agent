"""write_file 工具测试：四档路径判定 + 工具层兜底 + 审批集成。

方案：docs/plans/2026-08-20-write_file工具方案.md（决策点 1-5 已批准）
关键：monkeypatch _PROJECT_ROOT 指向 tmp_path——项目内/外可控测试
"""


import pytest

import qi_agent.tools.builtin.write_file as wf
from qi_agent.tools.builtin.write_file import write_file


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """把"项目根"指向临时目录：项目内 = tmp_path 下。"""
    monkeypatch.setattr(wf, "_PROJECT_ROOT", tmp_path)
    return tmp_path


def test_write_new_file_inside(project) -> None:
    """项目内新增 → 自动写入成功（UTF-8 内容）。"""
    path = project / "hello.txt"
    result = write_file(str(path), "你好，世界\nline2")
    assert "已写入" in result
    assert path.read_text(encoding="utf-8") == "你好，世界\nline2"


def test_write_overwrite_needs_approval(project) -> None:
    """覆盖已有文件 → 无 approved 拒绝（工具层兜底）。"""
    path = project / "x.txt"
    path.write_text("old", encoding="utf-8")
    result = write_file(str(path), "new")
    assert "[安全拦截]" in result
    assert "审批" in result
    assert path.read_text(encoding="utf-8") == "old"  # 未覆盖


def test_write_overwrite_approved(project) -> None:
    """覆盖 + approved=True → 写入成功。"""
    path = project / "x.txt"
    path.write_text("old", encoding="utf-8")
    result = write_file(str(path), "new", approved=True)
    assert "已写入" in result
    assert path.read_text(encoding="utf-8") == "new"


def test_write_outside_project(tmp_path, project) -> None:
    """项目外路径 → 无 approved 拒绝。"""
    outside = tmp_path.parent / "outside.txt"  # tmp_path 父目录 = 项目外
    result = write_file(str(outside), "x")
    assert "[安全拦截]" in result
    assert not outside.exists()


def test_write_sensitive_blocked(project) -> None:
    """敏感路径（.env）→ [安全拦截]（即使 approved=True 也拒——红线）。"""
    path = project / ".env"
    result = write_file(str(path), "KEY=xxx", approved=True)
    assert "[安全拦截]" in result
    assert not path.exists()


def test_write_traversal_escape(project) -> None:
    """../ 逃逸路径 → 视为项目外（resolve 后 is_relative_to 防逃逸）。"""
    escape = str(project / ".." / "escape.txt")
    result = write_file(escape, "x")
    assert "[安全拦截]" in result


def test_write_missing_dir_created(project) -> None:
    """目标目录不存在 → 自动创建（os.makedirs exist_ok）。"""
    path = project / "sub" / "deep" / "f.txt"
    result = write_file(str(path), "deep")
    assert "已写入" in result
    assert path.read_text(encoding="utf-8") == "deep"


# ── security_guard 四档判定 ───────────────────────────────────────────────


def test_security_guard_write_classify(project) -> None:
    """write_file 四档判定：红线/覆盖审批/越界审批/新增放行。"""
    from qi_agent.plugins.builtin.security_guard import SecurityGuardPlugin
    from qi_agent.tools.decision import ToolAction

    plugin = SecurityGuardPlugin()
    # 红线：敏感路径 → BLOCK
    r = plugin._on_tool_call("write_file", {"path": str(project / ".env")})
    assert r.action == ToolAction.BLOCK
    # 新增（不存在）→ None 放行
    r = plugin._on_tool_call(
        "write_file", {"path": str(project / "new.txt")})
    assert r is None
    # 覆盖（存在）→ NEED_APPROVAL
    (project / "exists.txt").write_text("x", encoding="utf-8")
    r = plugin._on_tool_call(
        "write_file", {"path": str(project / "exists.txt")})
    assert r.action == ToolAction.NEED_APPROVAL
    # 越界 → NEED_APPROVAL
    r = plugin._on_tool_call(
        "write_file", {"path": str(project.parent / "outside.txt")})
    assert r.action == ToolAction.NEED_APPROVAL


# ── 审批集成 ──────────────────────────────────────────────────────────────


def test_write_approval_flow(project) -> None:
    """集成：覆盖 → 审批事件 → 同意 → 写入（FakeClient）。"""
    from qi_agent.agents.agent import Agent
    from qi_agent.events import EventBus
    from qi_agent.llm import ChatResult, ToolCall
    from qi_agent.plugins.builtin.approval_gate import ApprovalGatePlugin
    from qi_agent.plugins.builtin.security_guard import SecurityGuardPlugin

    target = project / "exists.txt"
    target.write_text("old", encoding="utf-8")

    class FakeWriteClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None) -> ChatResult:
            self.calls += 1
            if self.calls == 1:
                return ChatResult(
                    content=None,
                    tool_calls=[ToolCall(
                        id="c1", name="write_file",
                        arguments={"path": str(target), "content": "new"},
                    )],
                    assistant_message={
                        "role": "assistant", "content": None,
                        "tool_calls": [{
                            "id": "c1", "type": "function",
                            "function": {"name": "write_file",
                                         "arguments": '{"path": "x", "content": "new"}'},
                        }],
                    },
                )
            return ChatResult(
                content="ok", tool_calls=None,
                assistant_message={"role": "assistant", "content": "ok"},
            )

    bus = EventBus()
    SecurityGuardPlugin().install(bus)
    ApprovalGatePlugin().install(bus)
    agent = Agent(FakeWriteClient(), events=bus)
    # 交互抽象层（2026-08-23）：注入假 provider 模拟用户同意
    from qi_agent.interaction import set_interaction_provider

    class _YesProvider:
        def ask(self, question, choices=None, timeout=None):
            return "y"

    set_interaction_provider(_YesProvider())
    try:
        agent.chat("覆盖文件")
    finally:
        set_interaction_provider(None)
    # 审批同意 → 写入成功
    tool_msgs = [m["content"] for m in agent.history if m["role"] == "tool"]
    assert any("已写入" in str(m) for m in tool_msgs)
    assert target.read_text(encoding="utf-8") == "new"


# ── schema 与注册 ─────────────────────────────────────────────────────────


def test_write_schema_no_approved() -> None:
    """schema 只含 path/content（approved 不暴露给模型）。"""
    from qi_agent.tools.registry import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY["write_file"]
    props = entry.schema["function"]["parameters"]["properties"]
    assert set(props) == {"path", "content"}
    required = entry.schema["function"]["parameters"]["required"]
    assert set(required) == {"path", "content"}


def test_write_registered() -> None:
    """write_file 应已注册。"""
    from qi_agent.tools.registry import _TOOL_REGISTRY

    assert "write_file" in _TOOL_REGISTRY
