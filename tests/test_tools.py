"""工具注册机制测试：@tool 装饰器、schema 生成、工具执行。"""

import re

from qi_agent.tools.registry import _TOOL_REGISTRY, execute_tool, get_tool_schemas, tool


def test_tool_registration() -> None:
    """@tool 装饰器应把函数登记进注册表。"""
    @tool(description="测试工具")
    def my_tool() -> str:
        return "ok"

    assert "my_tool" in _TOOL_REGISTRY
    assert _TOOL_REGISTRY["my_tool"].description == "测试工具"
    # 清理注册表，避免污染其他测试
    _TOOL_REGISTRY.pop("my_tool")


def test_tool_schema_no_params() -> None:
    """无参函数的 schema：properties 为空、required 为空。"""
    @tool(description="无参工具")
    def no_args() -> str:
        return "ok"

    schema = get_tool_schemas()
    my_schema = next(s for s in schema if s["function"]["name"] == "no_args")
    func = my_schema["function"]
    assert func["description"] == "无参工具"
    assert func["parameters"]["type"] == "object"
    assert func["parameters"]["properties"] == {}
    assert func["parameters"]["required"] == []
    _TOOL_REGISTRY.pop("no_args")


def test_tool_schema_with_params() -> None:
    """有参函数：schema 应含参数类型和 required。"""
    @tool(description="读文件")
    def read_it(path: str, max_lines: int = 10) -> str:
        return f"{path}:{max_lines}"

    schema = get_tool_schemas()
    my_schema = next(s for s in schema if s["function"]["name"] == "read_it")
    params = my_schema["function"]["parameters"]
    assert params["properties"]["path"]["type"] == "string"
    assert params["properties"]["max_lines"]["type"] == "integer"
    assert params["required"] == ["path"]  # 无默认值必填，有默认值可选
    _TOOL_REGISTRY.pop("read_it")


def test_execute_tool_success() -> None:
    """execute_tool 应执行工具并返回字符串结果。"""
    @tool(description="拼接")
    def concat(a: str, b: str) -> str:
        return a + b

    result = execute_tool("concat", {"a": "你好", "b": "世界"})
    assert result == "你好世界"
    _TOOL_REGISTRY.pop("concat")


def test_execute_unknown_tool() -> None:
    """调用不存在的工具应返回错误提示而非崩溃。"""
    result = execute_tool("no_such_tool", {})
    assert "未知工具" in result


def test_tool_name_is_function_name() -> None:
    """函数名即工具名。"""
    @tool(description="名字测试")
    def awesome_tool() -> str:
        return "ok"

    assert "awesome_tool" in _TOOL_REGISTRY
    _TOOL_REGISTRY.pop("awesome_tool")


def test_shell_blocks_dangerous_commands() -> None:
    """shell 工具应拒绝危险命令（read-only 白名单）。"""
    from qi_agent.tools.shell import shell

    assert "拒绝" in shell("rm -rf /")
    assert "拒绝" in shell("del C:\\Windows")
    assert "拒绝" in shell("format C:")
    assert "拒绝" in shell("shutdown /s")


def test_shell_allows_readonly_commands() -> None:
    """shell 工具应允许只读命令。"""
    from qi_agent.tools.shell import shell

    result = shell("pwd")
    assert isinstance(result, str) and len(result) > 0


def test_read_file_content() -> None:
    """read_file 应返回文件内容。"""
    from qi_agent.tools.read_file import read_file

    content = read_file("docs/python-basics/README.md")
    assert "Python" in content


def test_read_file_missing() -> None:
    """read_file 读取不存在的文件应返回错误提示。"""
    from qi_agent.tools.read_file import read_file

    result = read_file("no_such_file_xyz.txt")
    assert "不存在" in result


def test_get_time_format() -> None:
    """get_time 应返回 YYYY-MM-DD HH:MM:SS 格式。"""
    from qi_agent.tools.get_time import get_time

    result = get_time()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", result)
