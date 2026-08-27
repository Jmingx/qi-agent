"""/resume + /new 会话命令测试（方案 2026-08-26-会话持久化）。

验证：/new 开新会话（换 context）、/resume 列出 + 恢复指定会话。
"""

import unittest.mock as mock

from qi_agent.agents.agent import Agent


class _FakeClient:
    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def _run_cli(inputs: list[str], plugins: list | None = None) -> None:
    """跑 CLI（mock build_runtime——不碰真实存储/LLM）。"""
    from qi_agent.cli import main

    agent = Agent(_FakeClient())
    inputs_iter = iter(inputs)

    # mock storage（内存假存储——不写真实 ~/.qi-agent）
    fake_store = mock.MagicMock()
    fake_store.list_sessions.return_value = []
    fake_store.load_session.return_value = {
        "id": "ctx_old", "title": "旧会话", "turn": 2,
        "usage": {}, "status": "completed", "phase": "done",
        "messages": [{"role": "system", "content": "sys"},
                     {"role": "user", "content": "你好"},
                     {"role": "assistant", "content": "你好！"}],
    }

    with mock.patch("builtins.input",
                    side_effect=lambda prompt="": next(inputs_iter)), \
            mock.patch("qi_agent.cli.build_runtime", return_value=type(
                "B", (), {
                    "manager": type("M", (), {
                        "get_context": lambda self, cid: agent.context,
                        "run": lambda self, cid, text, stream_callback=None:
                            agent.chat(text),
                        "poll": lambda self, cid: None,
                        "stop": lambda self, cid: True,
                        "register": lambda self, ctx, role="main": ctx.id,
                        "unregister": lambda self, cid: None,
                    })(),
                    "context_id": "ctx_main", "installed": plugins or [],
                    "get_context": lambda self: agent.context,
                })()), \
            mock.patch("qi_agent.storage.get_storage", return_value=fake_store):
        main(argv=[])


def test_resume_lists_sessions() -> None:
    """/resume 无参 → 列出历史会话。"""
    _run_cli(["/resume", "exit"])  # 不崩溃即可（list 被 mock）


def test_new_starts_fresh() -> None:
    """/new → 开新会话（不崩溃 + 正常继续）。"""
    _run_cli(["/new", "你好", "exit"])


def test_resume_loads_session() -> None:
    """/resume <id> → 恢复指定会话（不崩溃）。"""
    _run_cli(["/resume ctx_old", "exit"])


def test_resume_not_found() -> None:
    """/resume 不存在 → 提示未找到（不崩溃）。"""
    _run_cli(["/resume nope", "exit"])


def test_remember_command_writes_memory() -> None:
    """/remember 命令行记忆：写 sticky + MEMORY.md。"""
    from qi_agent.context.sticky import get_sticky_text

    # 用临时记忆目录（防污染真实 ~/.qi-agent）
    import tempfile

    import qi_agent.storage.memory_store as ms

    tmp = tempfile.mkdtemp()
    orig_dir = ms._DEFAULT_DIR
    ms._DEFAULT_DIR = tmp
    try:
        _run_cli(["/remember 用户叫王五", "exit"])
        # sticky（会话内）
        assert "用户叫王五" in get_sticky_text()
        # MEMORY.md（跨会话）
        store = ms.MemoryStore()
        assert any("用户叫王五" in e for e in store.list_entries("memory"))
    finally:
        ms._DEFAULT_DIR = orig_dir
