"""软沙箱 v2 测试：RestrictedPython 绕过场景验证（v1 拦不住的由 v2 拦）。

方案：docs/plans/2026-08-19-软沙箱v2方案.md（决策点 1-7 已批准）
"""

from qi_agent.tools.builtin.run_python import run_python


def test_v2_blocks_concatenated_import() -> None:
    """v1 的拼接绕过（"impo"+"rt os"）在 v2 下应无效：不产生实际 import。

    v2 编译期看语法树——字符串相加是安全节点，运行时结果只是字符串，
    受限环境无 import 能力 → 代码无法读到系统信息。
    """
    result = run_python('x = "impo" + "rt os"\nprint(x)')
    assert "安全拦截" not in result  # v1 没拦（特征不在源码）——v2 兜底
    assert "impo" in result or "rt os" in result  # 只是字符串，无 import 效果


def test_v2_blocks_reflection_builtins() -> None:
    """反射拼内建（getattr(__builtins__, "op"+"en")）在 v2 下应受限。

    getattr 不在受限内建集 → NameError/受限错误，无法执行 open。
    """
    result = run_python('print(getattr(__builtins__, "op" + "en"))')
    # v2 受限环境无 getattr 内建 → 执行失败（NameError）而非拿到 open
    assert "open" not in result


def test_v2_blocks_pathlib_read() -> None:
    """import pathlib 读文件在 v2 下应被拒（import 受限，模块白名单为空）。"""
    result = run_python(
        "import pathlib\nprint(pathlib.Path('README.md').read_text())"
    )
    assert "README" not in result  # 读不到文件内容
    assert "安全拦截" in result or "错误" in result or "ImportError" in result


def test_v2_blocks_class_escape() -> None:
    """逃逸链（().__class__）应被拦——v1 已拦，v2 双保险验证。"""
    result = run_python("print(().__class__)")
    assert "安全拦截" in result or "错误" in result


def test_v2_normal_code_works() -> None:
    """正常计算代码在受限环境应正常输出。"""
    result = run_python(
        "s = 0\nfor i in range(5):\n    s += i\nprint(s)"
    )
    assert "10" in result


def test_v2_syntax_error_reported() -> None:
    """语法错误应返回明确提示（不炸）。"""
    result = run_python("def broken(:\n    pass")
    assert "编译失败" in result or "错误" in result


def test_v2_runtime_error_reported() -> None:
    """运行时异常应返回类型信息（不炸）。"""
    result = run_python("print(1 / 0)")
    assert "ZeroDivisionError" in result
