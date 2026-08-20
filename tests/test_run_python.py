"""run_python 软沙箱测试：四锁设计验证（权限/隔离/时间/安全锁）。"""

import os

import pytest

from qi_agent.tools.registry import _TOOL_REGISTRY
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


# ── env 双重过滤（方案 v0.4.12：密钥子串拦截 + 白名单保留，先黑后白）──────────


def _build_env_with(monkeypatch, **vars) -> dict:
    """构造含指定变量的环境并构建沙箱 env。"""
    from qi_agent.tools.run_python import _build_safe_env

    for k, v in vars.items():
        monkeypatch.setenv(k, v)
    return _build_safe_env()


def test_env_blocks_api_key_substring(monkeypatch) -> None:
    """变量名含 KEY（DEEPSEEK_API_KEY/MY_KEY）应被丢弃。"""
    env = _build_env_with(monkeypatch, DEEPSEEK_API_KEY="sk-xxx", MY_KEY="123")
    assert "DEEPSEEK_API_KEY" not in env
    assert "MY_KEY" not in env


def test_env_blocks_token_secret_password(monkeypatch) -> None:
    """TOKEN/SECRET/PASSWORD/AUTH/DSN/WEBHOOK/CREDENTIAL 应被丢弃。"""
    env = _build_env_with(
        monkeypatch,
        GITHUB_TOKEN="t", DB_SECRET="s", DB_PASSWORD="p",
        AUTH_TOKEN="a", DATABASE_DSN="d", SLACK_WEBHOOK="w",
        AWS_CREDENTIALS="c",
    )
    for name in ("GITHUB_TOKEN", "DB_SECRET", "DB_PASSWORD", "AUTH_TOKEN",
                 "DATABASE_DSN", "SLACK_WEBHOOK", "AWS_CREDENTIALS"):
        assert name not in env


def test_env_blocks_abbreviations(monkeypatch) -> None:
    """缩写写法（CREDS/BEARER/APIKEY，无下划线）应被丢弃（对齐 Hermes 踩坑）。"""
    env = _build_env_with(
        monkeypatch,
        MY_CREDS="c", BEARER="b", APIKEY="k",
    )
    assert "MY_CREDS" not in env
    assert "BEARER" not in env
    assert "APIKEY" not in env


def test_env_blocks_case_insensitive(monkeypatch) -> None:
    """小写变量名（api_key/my_token）应被丢弃（upper 扫描，Windows 友好）。"""
    env = _build_env_with(monkeypatch, api_key="x", my_token="y")
    assert "api_key" not in env
    assert "my_token" not in env


def test_env_keeps_whitelist(monkeypatch) -> None:
    """白名单变量（PATH/TEMP）应保留（行为零变化）。"""
    env = _build_env_with(monkeypatch, PATH="C:\\bin", TEMP="C:\\temp")
    assert env.get("PATH") == "C:\\bin"
    assert env.get("TEMP") == "C:\\temp"


def test_pass_not_in_substrings() -> None:
    """'PASS' 故意不在密钥子串清单（避免误伤 BYPASS_CACHE 等，Hermes 踩坑）。

    注意：BYPASS_CACHE 虽含 PASS 但会被白名单丢弃（不在 _SAFE_ENV_KEYS）——
    无法用"保留"验证；直接断言常量不含 PASS 才是设计意图的准确验证。
    """
    from qi_agent.tools.run_python import _SENSITIVE_KEY_SUBSTRINGS

    assert "PASS" not in _SENSITIVE_KEY_SUBSTRINGS
    assert "PASSWD" in _SENSITIVE_KEY_SUBSTRINGS  # PASSWD（密码缩写）该拦


def test_env_blocks_real_api_key_blacklist_first(monkeypatch) -> None:
    """第一道防线独立工作：密钥特征变量即使不在白名单外也先被拦。"""
    env = _build_env_with(
        monkeypatch,
        DEEPSEEK_API_KEY="sk-real",
        PATH="C:\\bin",
    )
    assert "DEEPSEEK_API_KEY" not in env
    assert env.get("PATH") == "C:\\bin"  # 白名单不受密钥拦截影响


def test_env_defense_in_depth(monkeypatch) -> None:
    """防御纵深核心价值：即使白名单被改宽（误加入密钥变量），密钥拦截仍生效。

    这是本方案（v0.4.12）与单层白名单的本质区别——模拟"将来名单失误"。
    """
    import qi_agent.tools.run_python as rp

    # 模拟未来白名单失误：把 DEEPSEEK_API_KEY 加进白名单
    monkeypatch.setattr(
        rp, "_SAFE_ENV_KEYS", rp._SAFE_ENV_KEYS + ("DEEPSEEK_API_KEY",)
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    env = rp._build_safe_env()
    # 第一道防线（密钥子串拦截）仍然拦住——这就是防御纵深
    assert "DEEPSEEK_API_KEY" not in env


# ── 工作目录隔离（方案 v0.4.16：临时目录执行，碰不到项目文件）───────────────


def _reload_rp(monkeypatch, **env) -> None:
    """清理注册表 + 设置环境变量 + 重载 run_python 模块。"""
    import importlib

    import qi_agent.tools.run_python as rp

    _TOOL_REGISTRY.pop("run_python", None)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(rp)
    return rp


@pytest.fixture(autouse=True)
def _restore_rp_default(monkeypatch):
    """测试结束后恢复默认 restricted 模式（避免污染其他测试）。"""
    yield
    _TOOL_REGISTRY.pop("run_python", None)
    monkeypatch.delenv("QI_SANDBOX_MODE", raising=False)
    import importlib

    import qi_agent.tools.run_python as rp

    importlib.reload(rp)


def test_cwd_isolated_legacy_write(monkeypatch) -> None:
    """legacy 模式拼接绕过写文件 → 项目根【没有】probe 文件（隔离证据）。

    v1 预检拦不住 getattr 拼接（清单无 getattr，且不含 "open(" 子串），
    legacy 完整 Python 会真执行 open——但 cwd 已是临时目录，
    文件写在临时目录而非项目根。
    """
    rp = _reload_rp(monkeypatch, QI_SANDBOX_MODE="legacy")
    probe = "probe_iso_write.txt"
    code = (
        "f = getattr(__builtins__, 'op' + 'en')"
        f"('{probe}', 'w'); f.write('x'); f.close()"
    )
    rp.run_python(code)
    try:
        assert not os.path.exists(probe), "隔离失效：文件写到了项目根！"
    finally:
        # 清理（若隔离失效导致残留，不能污染项目）
        if os.path.exists(probe):
            os.unlink(probe)


def test_cwd_isolated_legacy_read(monkeypatch) -> None:
    """legacy 拼接读项目文件 → 读不到（临时目录无该文件）。

    用 pyproject.toml（项目根真实存在）——未隔离时能读到内容，
    隔离后临时目录没有它 → FileNotFoundError。
    """
    rp = _reload_rp(monkeypatch, QI_SANDBOX_MODE="legacy")
    code = (
        "f = getattr(__builtins__, 'op' + 'en')('pyproject.toml'); "
        "print(f.read()); f.close()"
    )
    result = rp.run_python(code)
    assert "FileNotFoundError" in result  # 临时目录没有 pyproject.toml


def test_normal_code_unaffected() -> None:
    """正常计算不受 cwd 隔离影响（回归，默认 restricted 模式）。"""
    result = run_python("print(1 + 1)")
    assert "2" in result


def test_tmpdir_cleaned() -> None:
    """执行后临时目录不残留（TemporaryDirectory 自动清理）。"""
    import glob

    import tempfile

    before = set(glob.glob(
        os.path.join(tempfile.gettempdir(), "qi_sandbox_*")
    ))
    run_python("print(42)")
    after = set(glob.glob(
        os.path.join(tempfile.gettempdir(), "qi_sandbox_*")
    ))
    assert before == after  # 无新增残留目录
