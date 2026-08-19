"""插件配置加载：读取 plugins.toml（可选文件），解析启用状态。

设计（方案 docs/plans/2026-08-19-插件装配升级方案.md）：
- 格式 TOML（决策点 2）：Python 3.11 内置 tomllib 只读解析，零新依赖
- 文件可选：不存在时返回空 dict → 全部回落插件 default_enabled（零配置可跑）
- 配置文件不进 git（用户偏好，同 .env 逻辑）；plugins.example.toml 进 git 作模板

plugins.toml 格式：
    [tool_stats]
    enabled = true

    [security_guard]   # 将来
    enabled = false
"""

import tomllib
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("plugins.toml")  # 项目根，可选存在


def load_plugin_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """读取插件配置。

    Args:
        path: 配置文件路径（默认项目根 plugins.toml）

    Returns:
        {插件名: {"enabled": bool}}；文件不存在时返回空 dict

    Raises:
        tomllib.TOMLDecodeError: 配置文件语法错误（原样抛出，明确报错）
    """
    if not path.exists():
        return {}
    with open(path, "rb") as f:  # tomllib 要求二进制模式读取
        return tomllib.load(f)
