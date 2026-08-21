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


def test_shell_utf8_output(tmp_path) -> None:
    """shell 读取非 GBK 字节不应因解码失败丢输出（Windows 编码坑）。

    回归：text=True 默认用系统 locale（Windows=GBK），0x80 是 GBK 非法
    首字节 → 触发 UnicodeDecodeError（子进程 reader 线程炸 → 输出变空）。
    修复：encoding="utf-8" + errors="replace"。
    """
    from qi_agent.tools.shell import shell

    f = tmp_path / "utf8.txt"
    f.write_bytes(b"a\x80b")  # 0x80 必然触发 GBK 解码失败
    result = shell(f"type {f}")
    # 修复前：reader 线程 UnicodeDecodeError → 输出为空/异常
    # 修复后：errors="replace" 保证不炸，ASCII 内容可读
    assert result != "(无输出)"
    assert "a" in result and "b" in result


def test_run_python_unicode_output() -> None:
    """run_python 执行含非 GBK 字符的输出不应炸（防御回归，与 shell 同修复）。

    子进程默认按系统 locale（GBK）编码 stdout，emoji 无法编码 →
    子进程内部报错。修复：-X utf8 强制子进程 UTF-8 模式 + 父进程 utf-8 解码。
    """
    from qi_agent.tools.run_python import run_python

    result = run_python("print('ok-emoji-\\U0001F600')")
    assert "ok-emoji" in result


def test_shell_long_running_timeout_not_hang(monkeypatch) -> None:
    """长驻命令（如启动游戏）超时后快速返回，不卡死（Windows 管道死锁修复 2026-08-21）。

    复现：subprocess.run(timeout=...) 超时后 kill 的只是 cmd 外壳——被启动的
    长驻程序（ping -t 模拟游戏）仍持有 stdout/stderr 管道 → run 内部第二次
    communicate() 等 EOF 永远等不到 → agent 卡死（实测 exit 124）。
    修复：Popen + communicate(timeout) + 超时杀进程树 + 立即返回。
    """
    import time

    import qi_agent.tools.shell as sh

    monkeypatch.setattr(sh, "_COMMAND_TIMEOUT", 2)  # 缩短超时加速测试
    t0 = time.time()
    # approved=True 跳过白名单（ping 不在只读白名单，模拟审批通过的启动命令）
    result = sh.shell("ping -t 127.0.0.1", approved=True)
    elapsed = time.time() - t0
    assert "超时" in result
    assert elapsed < 10  # 修复前永久卡死；修复后 ~2s 返回


def _kill_pid_tree(pid: int) -> None:
    """清理测试启动的常驻进程树（ping -t 不退出，必须杀防残留）。"""
    import psutil

    try:
        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            child.kill()
        proc.kill()
    except psutil.Error:
        pass  # 已退出


def test_shell_background_returns_fast() -> None:
    """background=True：常驻进程（ping -t 模拟游戏）立即返回，不阻塞对话。"""
    import re
    import time

    from qi_agent.tools.shell import shell

    t0 = time.time()
    result = shell("ping -t 127.0.0.1", approved=True, background=True)
    elapsed = time.time() - t0
    assert "已启动" in result
    assert elapsed < 2  # 异步立即返回（同步路径会 10s 超时）
    # 清理常驻进程（防残留）
    pid = int(re.search(r"PID (\d+)", result).group(1))
    _kill_pid_tree(pid)


def test_shell_background_process_alive() -> None:
    """background=True 返回的 PID 进程真实存在（异步启动成功）。"""
    import re
    import time

    import psutil

    from qi_agent.tools.shell import shell

    result = shell("ping -t 127.0.0.1", approved=True, background=True)
    pid = int(re.search(r"PID (\d+)", result).group(1))
    time.sleep(0.3)  # 给进程一点启动时间
    assert psutil.pid_exists(pid)
    _kill_pid_tree(pid)
