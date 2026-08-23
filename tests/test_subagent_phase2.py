"""Phase 2 测试：delegate_task 工具（嵌套 Agent + 结构化 result + 授权清单）。

方案：docs/plans/2026-08-23-subagent方案.md 第 2/4 节
- agent-as-tool：delegate_task 注册为普通工具，主 agent 自己决定外包
- 结构化返回：result 是 JSON（summary/artifacts/status/error/question/usage）
- 受限子集：子 agent 只见白名单工具（Phase 1 双层）
- 授权清单：write_paths 白名单匹配，子 agent 不弹窗
"""

import json


import qi_agent.tools.builtin.delegate_task as dt
from qi_agent.tools.registry import get_tool


class _FakeSubClient:
    """假子 agent client：一次对话返回结构化结果（模拟子 agent 完成）。"""

    def __init__(self, summary: str = "调研完成，发现 3 个关键点") -> None:
        self.summary = summary

    def chat(self, messages, tools=None):
        """返回一个最终回答（结构化 JSON 文本）。"""
        from qi_agent.llm import ChatResult

        structured = json.dumps(
            {"summary": self.summary, "artifacts": ["subagent/out.md"],
             "status": "completed", "error": None, "question": None},
            ensure_ascii=False,
        )
        return ChatResult(
            content=structured, tool_calls=None,
            assistant_message={"role": "assistant", "content": structured},
        )


def _make_task(tools: list[str] | None = None) -> dt.delegate_task:
    """构造 delegate_task（注入假 client 工厂 + 假 tool executor）。"""
    factory_calls = []

    def _factory():
        factory_calls.append(1)
        return _FakeSubClient()

    return dt.delegate_task(
        goal="调研 XX 项目",
        context="项目背景：qi-agent，Python 项目",
        tools=tools,
        write_paths=["C:/tmp/subagent_out"],
        _client_factory=_factory,
    )


class TestRegistration:
    def test_registered(self) -> None:
        """delegate_task 已注册为工具。"""
        entry = get_tool("delegate_task")
        assert entry is not None
        assert "goal" in entry.schema["function"]["parameters"]["properties"]

    def test_approval_is_rule_name_not_lambda(self) -> None:
        """审批声明 = 规则名（v0.4.27 规则化）——条件逻辑在 rules 表，
        不在工具文件硬编码 lambda。"""
        entry = get_tool("delegate_task")
        assert entry is not None
        assert entry.approval == "subagent"
        assert not callable(entry.approval)  # 不是 callable（硬编码）


class TestStructuredResult:
    def test_returns_structured_json(self) -> None:
        """delegate_task 返回结构化 JSON（summary/artifacts/status）。"""
        output = _make_task()
        assert isinstance(output, str)
        data = json.loads(output)
        assert data["status"] == "completed"
        assert "调研完成" in data["summary"]
        assert "subagent/out.md" in data["artifacts"]

    def test_schema_keys_present(self) -> None:
        """结构化 schema 关键字段齐全（P0 用户要求）。"""
        output = _make_task()
        data = json.loads(output)
        for key in ("summary", "artifacts", "status", "error", "question", "usage"):
            assert key in data, f"缺少字段 {key}"


class TestRestrictedTools:
    def test_subagent_sees_only_allowlist(self) -> None:
        """子 agent 只看到白名单工具（层 1）。"""
        seen_tools: list = []

        class _RecordingClient(_FakeSubClient):
            def chat(self, messages, tools=None):
                seen_tools.append(tools or [])
                return super().chat(messages, tools)

        def _factory():
            return _RecordingClient()

        dt.delegate_task(
            goal="调研",
            tools=["read_file", "get_time"],
            _client_factory=_factory,
        )
        assert seen_tools, "子 agent 必须发起至少一次 LLM 调用"
        names = {s["function"]["name"] for s in seen_tools[-1]}
        # 指定工具 + 默认只读子集（并集），危险工具永远排除
        assert {"read_file", "get_time"} <= names
        assert "shell" not in names

    def test_recursion_forbidden_structurally(self) -> None:
        """递归禁止（结构层）：子 agent 工具集里永远没有 delegate_task。

        防线 1（结构禁止）——子 agent 物理上无法再 spawn 孙 agent：
        即使主 agent 显式请求 delegate_task，也被 _FORBIDDEN_TOOLS 剔除。
        """
        seen_tools: list = []

        class _RecordingClient(_FakeSubClient):
            def chat(self, messages, tools=None):
                seen_tools.append(tools or [])
                return super().chat(messages, tools)

        def _factory():
            return _RecordingClient()

        # 主 agent 恶意/误请求 delegate_task + shell——都必须被剔除
        dt.delegate_task(
            goal="调研",
            tools=["delegate_task", "shell", "read_file"],
            _client_factory=_factory,
        )
        names = {s["function"]["name"] for s in seen_tools[-1]}
        assert "delegate_task" not in names  # 递归禁止（结构层）
        assert "shell" not in names          # 危险工具永远排除
        assert "read_file" in names          # 安全工具保留

    def test_forbidden_tools_never_in_subset(self) -> None:
        """层 3 硬编码：_FORBIDDEN_TOOLS 全部工具在默认子集不可见。"""
        from qi_agent.tools.builtin.delegate_task import (
            DEFAULT_READONLY_TOOLS, _FORBIDDEN_TOOLS,
        )

        for t in _FORBIDDEN_TOOLS:
            assert t not in DEFAULT_READONLY_TOOLS, f"{t} 不应在默认子集"


class TestApproval:
    def test_write_inside_allowlist_allowed(self) -> None:
        """子 agent 写白名单内路径 → 放行（授权清单匹配）。"""
        ok = dt._approve_tool("write_file",
                              {"path": "C:/tmp/subagent_out/a.md"}, ["C:/tmp/subagent_out"])
        assert ok is True

    def test_write_outside_allowlist_denied(self) -> None:
        """子 agent 写白名单外路径 → 拒绝（fail-closed）。"""
        ok = dt._approve_tool("write_file",
                              {"path": "C:/Windows/system32/x.md"}, ["C:/tmp/subagent_out"])
        assert ok is False

    def test_no_write_paths_denies_write(self) -> None:
        """write_paths 为空 → write_file 一律拒绝（安全底线）。"""
        ok = dt._approve_tool("write_file",
                              {"path": "C:/tmp/x.md"}, [])
        assert ok is False

    def test_readonly_tools_allowed_without_write_paths(self) -> None:
        """只读工具在无写权限时也放行。"""
        ok = dt._approve_tool("read_file", {"path": "C:/a.txt"}, [])
        assert ok is True
