"""run_python 软沙箱测试：四锁设计验证（权限/隔离/时间/安全锁）。"""

from qi_agent.tools.run_python import run_python


def test_execute_simple_code() -> None:
    """基本执行：print 输出应返回。"""
    result = run_python("print(1 + 1)")
    assert "2" in result


def test_execute_returns_stdout() -> None:
    """多行代码输出。"""
    result = run_python("for i in range(3):\n    print(i)")
    assert "0" in result and "1" in result and "2" in result


def test_block_import_os() -> None:
    """import os 应被拦截。"""
    result = run_python("import os\nprint(os.getcwd())")
    assert "安全拦截" in result
    assert "import os" in result


def test_block_import_subprocess() -> None:
    """import subprocess 应被拦截。"""
    result = run_python("import subprocess")
    assert "安全拦截" in result


def test_block_open() -> None:
    """open( 应被拦截（防文件访问）。"""
    result = run_python("f = open('secret.txt')")
    assert "安全拦截" in result
    assert "open(" in result


def test_block_reflection() -> None:
    """反射逃逸链应被拦截。"""
    result = run_python("print(().__class__)")
    assert "安全拦截" in result


def test_block_eval_exec() -> None:
    """eval( 和 exec( 应被拦截。"""
    assert "安全拦截" in run_python("eval('1+1')")
    assert "安全拦截" in run_python("exec('x=1')")


def test_timeout_infinite_loop() -> None:
    """死循环应触发超时。"""
    result = run_python("while True:\n    pass")
    assert "超时" in result


def test_safe_env_no_api_key() -> None:
    """安全锁：白名单环境变量不应包含 DEEPSEEK_API_KEY。"""
    # 直接验证白名单构建函数（比通过沙箱执行更干净——
    # 沙箱内 import os 会被权限锁拦截，无法用代码读环境变量）
    from qi_agent.tools.run_python import _build_safe_env

    env = _build_safe_env()
    assert "DEEPSEEK_API_KEY" not in env
    assert "PATH" in env  # OS 必需变量保留
    # 白名单之外的关键变量也应被丢弃
    assert "HERMES_HOME" not in env


def test_output_truncated() -> None:
    """超长输出应被截断。"""
    result = run_python("print('x' * 5000)")
    assert "截断" in result
    assert len(result) < 3000


def test_registered_in_registry() -> None:
    """run_python 应已注册且 schema 正确。"""
    from qi_agent.tools.registry import _TOOL_REGISTRY

    assert "run_python" in _TOOL_REGISTRY
    entry = _TOOL_REGISTRY["run_python"]
    assert entry.toolset == "builtin"
    # 参数 schema 应包含 code
    params = entry.schema["function"]["parameters"]["properties"]
    assert "code" in params
    assert entry.schema["function"]["parameters"]["required"] == ["code"]
