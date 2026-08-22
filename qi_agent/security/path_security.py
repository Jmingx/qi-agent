"""路径安全检查：敏感路径黑名单 + 路径规范化。

设计（回顾方案 + 配置化讨论）：
- 敏感名单是"安全底线"，保持硬编码——不让用户/agent 配置改掉
  （配置化只用于"用户决策类"，如审批白名单）
- 路径规范化（abspath）防 `..` 绕过
- 三段检查：文件名 / 目录段 / 扩展名
- Windows 8.3 短文件名（v0.4.23 评测 s2 暴露）：`GIT~1` = `.git` 的短名，
  abspath 不解析短名 → 需显式检测 8.3 格式（保守拦截，任何段匹配即拒）
"""

import os
import re

# 敏感文件名（任意目录下命中即拒绝，小写比较）
_SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "id_dsa", "known_hosts",
    ".netrc", ".npmrc", ".pypirc", ".htpasswd",
}

# 敏感目录名（路径中任一段命中即拒绝）
_SENSITIVE_DIRS = {
    ".git", ".ssh", "node_modules", "__pycache__", ".venv", "venv",
}

# 敏感文件扩展名
_SENSITIVE_EXTENSIONS = {
    ".pem", ".key", ".p12", ".pfx", ".keystore", ".jks",
}

# Windows 8.3 短文件名格式：1-6 字符 + ~ + 数字（如 GIT~1、PROGRA~1）
# 短名是长名的别名（abspath 不解析）——敏感目录的短名可绕过黑名单，
# 保守策略：任何段匹配 8.3 格式即拦截（正常用户极少用短名路径）
_SHORT_NAME_83_RE = re.compile(r"^[a-z0-9]{1,6}~\d+$", re.IGNORECASE)


def is_sensitive_path(path: str) -> bool:
    """判断路径是否敏感（规范化后检查）。

    检查流程：
    1. os.path.abspath 规范化（防 ../ 相对路径绕过）
    2. 按 / 拆分段（Windows 反斜杠也转）
    3. 文件名检查 → 目录段检查 → 扩展名检查 → 8.3 短名检查

    Args:
        path: 用户提供的路径（相对/绝对均可）

    Returns:
        True=敏感（应拒绝），False=安全
    """
    abs_path = os.path.abspath(path)
    # 统一分隔符并拆分（Windows C:\\x → C:/x → ["C:", "x"]）
    parts = abs_path.replace("\\", "/").split("/")

    filename = parts[-1].lower()
    if filename in _SENSITIVE_NAMES:
        return True
    # 目录段检查（排除盘符和空段）
    if any(p.lower() in _SENSITIVE_DIRS for p in parts):
        return True
    if any(filename.endswith(ext) for ext in _SENSITIVE_EXTENSIONS):
        return True
    # 8.3 短文件名检查（v0.4.23 P0 修复：评测 s2 暴露 GIT~1 绕过 .git 黑名单）
    # 任何段（含文件名）匹配 8.3 格式即拒——宁可误伤（短名路径极少正常使用）
    if any(_SHORT_NAME_83_RE.match(p) for p in parts):
        return True
    return False
