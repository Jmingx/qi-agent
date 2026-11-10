"""冒烟测试：验证包可正常导入、版本号正确。"""

import tomllib
from pathlib import Path

# 项目根（tests/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_package_imports() -> None:
    """验证 qi_agent 包可导入且版本号与 pyproject.toml 同步。

    防脱节：__version__ 与 pyproject version 曾长期不一致（停在 0.1.0
    而 git tag 已到 v0.4.x）——两边必须一致，升级版本时同步改，任一边
    单独改动本测试立即红。
    """
    import qi_agent

    with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
        pyproject_version = tomllib.load(f)["project"]["version"]
    assert qi_agent.__version__ == pyproject_version
