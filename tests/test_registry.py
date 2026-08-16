"""register() 显式注册机制测试：ToolEntry 结构、check_fn、requires_env、toolset。"""

import pytest

from qi_agent.tools.registry import (
    _TOOL_REGISTRY,
    execute_tool,
    get_tool_schemas,
    get_tools_by_toolset,
    register,
    tool,
)


def _cleanup(name: str) -> None:
    """测试后清理注册表。"""
    _TOOL_REGISTRY.pop(name, None)


def test_register_explicit() -> None:
    """register() 注册后工具可用、schema 自动生成。"""
    def my_tool(a: int) -> str:
        return f"a={a}"

    register(name="my_tool", handler=my_tool, description="测试工具")
    try:
        assert "my_tool" in _TOOL_REGISTRY
        assert _TOOL_REGISTRY["my_tool"].description == "测试工具"
        assert _TOOL_REGISTRY["my_tool"].toolset == "builtin"  # 默认分组
        assert execute_tool("my_tool", {"a": 5}) == "a=5"
        # schema 自动生成
        assert _TOOL_REGISTRY["my_tool"].schema["function"]["name"] == "my_tool"
    finally:
        _cleanup("my_tool")


def test_register_with_custom_schema() -> None:
    """手写 schema 应被使用（不自动生成）。"""
    def my_tool(x: str) -> str:
        return x

    custom_schema = {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "手写描述",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        },
    }
    register(name="my_tool", handler=my_tool, schema=custom_schema)
    try:
        assert _TOOL_REGISTRY["my_tool"].schema == custom_schema
    finally:
        _cleanup("my_tool")


def test_register_duplicate_raises() -> None:
    """重复注册同名工具应抛错。"""
    def tool_a() -> str:
        return "a"

    register(name="dup_tool", handler=tool_a)
    try:
        with pytest.raises(ValueError, match="已存在"):
            register(name="dup_tool", handler=tool_a)
    finally:
        _cleanup("dup_tool")


def test_register_with_check_fn_false() -> None:
    """check_fn 返回 False 时不应注册。"""
    def my_tool() -> str:
        return "ok"

    register(name="check_fail_tool", handler=my_tool, check_fn=lambda: False)
    assert "check_fail_tool" not in _TOOL_REGISTRY


def test_register_with_check_fn_true() -> None:
    """check_fn 返回 True 时应注册。"""
    def my_tool() -> str:
        return "ok"

    register(name="check_ok_tool", handler=my_tool, check_fn=lambda: True)
    try:
        assert "check_ok_tool" in _TOOL_REGISTRY
    finally:
        _cleanup("check_ok_tool")


def test_register_requires_env_missing(monkeypatch) -> None:
    """requires_env 指定的环境变量缺失时不应注册。"""
    def my_tool() -> str:
        return "ok"

    monkeypatch.delenv("SOME_NEEDED_ENV", raising=False)
    register(name="env_tool", handler=my_tool, requires_env=["SOME_NEEDED_ENV"])
    assert "env_tool" not in _TOOL_REGISTRY


def test_register_requires_env_present(monkeypatch) -> None:
    """requires_env 指定的环境变量存在时应注册。"""
    def my_tool() -> str:
        return "ok"

    monkeypatch.setenv("SOME_NEEDED_ENV", "1")
    register(name="env_ok_tool", handler=my_tool, requires_env=["SOME_NEEDED_ENV"])
    try:
        assert "env_ok_tool" in _TOOL_REGISTRY
    finally:
        _cleanup("env_ok_tool")


def test_tool_decorator_backward_compat() -> None:
    """@tool 旧用法仍工作（向后兼容回归）。"""
    @tool(description="兼容测试")
    def legacy_tool() -> str:
        return "legacy"

    try:
        assert "legacy_tool" in _TOOL_REGISTRY
        assert _TOOL_REGISTRY["legacy_tool"].description == "兼容测试"
        assert execute_tool("legacy_tool", {}) == "legacy"
    finally:
        _cleanup("legacy_tool")


def test_toolset_grouping() -> None:
    """不同 toolset 的工具能分组查询。"""
    def tool_a() -> str:
        return "a"

    def tool_b() -> str:
        return "b"

    register(name="grp_a", handler=tool_a, toolset="groupA")
    register(name="grp_b", handler=tool_b, toolset="groupB")
    try:
        names_a = get_tools_by_toolset("groupA")
        names_b = get_tools_by_toolset("groupB")
        assert "grp_a" in names_a and "grp_b" not in names_a
        assert "grp_b" in names_b and "grp_a" not in names_b
    finally:
        _cleanup("grp_a")
        _cleanup("grp_b")


def test_get_tool_schemas_contains_all() -> None:
    """get_tool_schemas 应返回所有已注册工具的 schema。"""
    def tool_x() -> str:
        return "x"

    register(name="schema_x", handler=tool_x)
    try:
        schemas = get_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "schema_x" in names
    finally:
        _cleanup("schema_x")
