"""沙箱配置测试：执行模式开关 + 内建集/import 白名单扩展。

方案：docs/plans/2026-08-19-软沙箱v2方案.md（决策点 4/5）
注意：环境变量在 import 时读取（模块级）——测试需清理注册表后 reload 模块。
"""

import importlib

import pytest

import qi_agent.tools.run_python as rp
from qi_agent.tools.registry import _TOOL_REGISTRY


def _reload(monkeypatch, **env) -> None:
    """清理注册表 + 设置环境变量 + 重载 run_python 模块。

    reload 会重新执行模块级 register()——必须先 pop 旧注册，否则重复注册报错。
    """
    _TOOL_REGISTRY.pop("run_python", None)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(rp)


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    """测试结束后恢复默认环境并重载模块（避免污染其他测试）。"""
    yield
    _TOOL_REGISTRY.pop("run_python", None)
    monkeypatch.delenv("QI_SANDBOX_MODE", raising=False)
    monkeypatch.delenv("QI_SANDBOX_EXTRA_BUILTINS", raising=False)
    monkeypatch.delenv("QI_SANDBOX_EXTRA_MODULES", raising=False)
    importlib.reload(rp)


def test_mode_default_restricted(monkeypatch) -> None:
    """未配置 QI_SANDBOX_MODE 应默认 restricted。"""
    _reload(monkeypatch)
    assert rp._SANDBOX_MODE == "restricted"


def test_mode_legacy_works(monkeypatch) -> None:
    """QI_SANDBOX_MODE=legacy 应进入 legacy 路径（现状行为）。"""
    _reload(monkeypatch, QI_SANDBOX_MODE="legacy")
    assert rp._SANDBOX_MODE == "legacy"
    result = rp.run_python("print(1 + 1)")
    assert "2" in result


def test_mode_invalid_falls_back(monkeypatch) -> None:
    """非法模式值应回落 restricted（最安全默认）。"""
    _reload(monkeypatch, QI_SANDBOX_MODE="whatever")
    assert rp._SANDBOX_MODE == "restricted"


def test_extra_builtins_allowed(monkeypatch) -> None:
    """QI_SANDBOX_EXTRA_BUILTINS 内的可放行内建应在沙箱可用。"""
    _reload(monkeypatch, QI_SANDBOX_EXTRA_BUILTINS="round,pow")
    result = rp.run_python("print(round(3.7), pow(2, 3))")
    assert "4" in result and "8" in result


def test_extra_builtins_not_in_allowlist(monkeypatch) -> None:
    """配置不在可放行清单的内建（open）应被忽略。"""
    _reload(monkeypatch, QI_SANDBOX_EXTRA_BUILTINS="open")
    result = rp.run_python("print(open('README.md'))")
    # open 不在受限内建 → NameError 或受限错误，且读不到文件
    assert "README" not in result


def test_extra_modules_allowed(monkeypatch) -> None:
    """QI_SANDBOX_EXTRA_MODULES=math 放行后 import math 可用。"""
    _reload(monkeypatch, QI_SANDBOX_EXTRA_MODULES="math")
    result = rp.run_python("import math\nprint(math.sqrt(9))")
    assert "3.0" in result


def test_extra_modules_not_in_allowlist(monkeypatch) -> None:
    """配置 os（不在可放行清单）应被忽略——import 仍受限。"""
    _reload(monkeypatch, QI_SANDBOX_EXTRA_MODULES="os")
    result = rp.run_python("import os\nprint(os.getcwd())")
    assert "安全拦截" in result or "ImportError" in result or "错误" in result


def test_default_import_blocked() -> None:
    """未配置扩展时 import math 应被拒（默认最严格）。"""
    result = rp.run_python("import math\nprint(math.sqrt(4))")
    assert "安全拦截" in result or "ImportError" in result or "错误" in result
