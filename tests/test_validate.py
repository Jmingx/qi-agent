"""工具参数校验测试：validate_arguments 三检查 + execute_tool 接入。

覆盖（方案 docs/plans/2026-08-17-参数校验方案.md，评审决策 1-5 已批准）：
- 必填检查 / 类型检查 / 多余参数检查
- bool 严格化（决策 4）：integer 参数拒绝 bool
- 集成：execute_tool 返回 [参数错误] 前缀
"""

import pytest

from qi_agent.tools.registry import (
    _TOOL_REGISTRY,
    execute_tool,
    register,
    validate_arguments,
)


@pytest.fixture()
def sample_tool_entry():
    """注册校验夹具工具（覆盖四种基础类型），测试结束清理。"""

    def sample_tool(
        name: str,
        count: int = 1,
        ratio: float = 0.5,
        enabled: bool = False,
    ) -> str:
        """返回拼接结果，验证参数透传。"""
        return f"{name}:{count}:{ratio}:{enabled}"

    register(name="sample_tool", handler=sample_tool, description="校验测试工具")
    yield _TOOL_REGISTRY["sample_tool"]
    _TOOL_REGISTRY.pop("sample_tool", None)


def test_valid_args_pass(sample_tool_entry) -> None:
    """正确参数应通过校验（返回 None）。"""
    schema = sample_tool_entry.schema
    assert validate_arguments(schema, {"name": "x", "count": 3}) is None


def test_missing_required(sample_tool_entry) -> None:
    """缺必填参数应返回含参数名的可行动错误。"""
    schema = sample_tool_entry.schema
    err = validate_arguments(schema, {"count": 3})
    assert err is not None
    assert "缺少必填参数" in err
    assert "name" in err


def test_wrong_type(sample_tool_entry) -> None:
    """类型错误应返回期望/实际类型。"""
    schema = sample_tool_entry.schema
    err = validate_arguments(schema, {"name": 123})
    assert err is not None
    assert "string" in err
    assert "int" in err


def test_extra_argument(sample_tool_entry) -> None:
    """多余参数应返回未知参数名 + 可用参数列表。"""
    schema = sample_tool_entry.schema
    err = validate_arguments(schema, {"name": "x", "hack": 1})
    assert err is not None
    assert "未知参数" in err
    assert "hack" in err
    assert "name" in err  # 可用参数列表里含 name


def test_no_params_tool_pass() -> None:
    """无参工具传空 dict 应通过。"""

    def no_args() -> str:
        return "ok"

    register(name="no_args_v", handler=no_args)
    try:
        assert validate_arguments(_TOOL_REGISTRY["no_args_v"].schema, {}) is None
    finally:
        _TOOL_REGISTRY.pop("no_args_v", None)


def test_optional_param_omitted(sample_tool_entry) -> None:
    """可选参数省略应通过。"""
    schema = sample_tool_entry.schema
    assert validate_arguments(schema, {"name": "x"}) is None


def test_integer_rejects_bool(sample_tool_entry) -> None:
    """bool 严格化（评审决策 4）：integer 参数传 True 应拒绝。"""
    schema = sample_tool_entry.schema
    err = validate_arguments(schema, {"name": "x", "count": True})
    assert err is not None
    assert "integer" in err
    assert "boolean" in err


def test_boolean_accepts_bool(sample_tool_entry) -> None:
    """回归：boolean 参数传 True 应通过。"""
    schema = sample_tool_entry.schema
    assert validate_arguments(schema, {"name": "x", "enabled": True}) is None


def test_number_accepts_int_float(sample_tool_entry) -> None:
    """number 类型应同时接受 int 和 float。"""
    schema = sample_tool_entry.schema
    assert validate_arguments(schema, {"name": "x", "ratio": 2}) is None
    assert validate_arguments(schema, {"name": "x", "ratio": 2.5}) is None


def test_execute_tool_validation_error(sample_tool_entry) -> None:
    """集成：execute_tool 传错参数应返回 [参数错误] 前缀。"""
    result = execute_tool("sample_tool", {})  # 缺必填 name
    assert result.startswith("[参数错误]")


def test_execute_tool_valid(sample_tool_entry) -> None:
    """集成：正确参数应正常执行。"""
    result = execute_tool("sample_tool", {"name": "hi"})
    assert result == "hi:1:0.5:False"
