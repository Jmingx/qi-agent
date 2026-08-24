"""安全规则模块完整性测试（方案 2026-08-22 权限规则统一）。

验证：规则单一来源模块导出完整 + 代码执行段与审批段不重复（死代码消除）。
行为回归由既有测试覆盖（security_guard 判档 / shell 硬拒 / path_security）。
"""

from qi_agent.security.rules import (
    APPROVAL_PREFIXES,
    CODE_EXEC_PREFIXES,
    HARDLINE_PREFIXES,
    SHELL_COMBINATOR_SYNTAX,
    TOOL_APPROVAL_RULES,
)


def test_rules_all_groups_present() -> None:
    """四个规则组导出完整且非空。"""
    assert "shutdown" in HARDLINE_PREFIXES
    assert "|" in SHELL_COMBINATOR_SYNTAX
    assert "rm " in APPROVAL_PREFIXES
    assert "python" in CODE_EXEC_PREFIXES


def test_code_exec_not_duplicated_in_approval() -> None:
    """代码执行段与审批段无交集（v0.4.23 前 python/pip/npm 双份死代码已消除）。"""
    overlap = set(CODE_EXEC_PREFIXES) & set(APPROVAL_PREFIXES)
    assert not overlap, f"重复条目: {overlap}"


def test_hardline_not_in_approval() -> None:
    """红线命令不在审批档（红线不可审批，永远无 approved 可注入）。"""
    overlap = set(HARDLINE_PREFIXES) & set(APPROVAL_PREFIXES)
    assert not overlap, f"红线混入审批档: {overlap}"


def test_tool_approval_rules_registered() -> None:
    """工具级审批规则表有 subagent 规则（v0.4.27 规则化）。"""
    assert "subagent" in TOOL_APPROVAL_RULES
    rule = TOOL_APPROVAL_RULES["subagent"]
    # 纯只读委派 → 放行（None）
    assert rule({"goal": "调研", "context": "背景"}) is None
    # 带写权限 → 审批描述（含写权限提示）
    desc = rule({"goal": "写代码", "context": "", "write_paths": ["C:/tmp/"]})
    assert desc is not None and "写权限" in desc
