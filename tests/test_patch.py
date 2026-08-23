"""patch 精确编辑工具测试：匹配策略链 + 原子性 + diff + 安全。

设计（方案 2026-08-22-工具三件套）：4 个保守匹配策略（exact → 行尾容错
→ 空白归一 → 缩进对齐），宁可报错不误改；匹配失败不写文件（原子性）；
编辑已有文件 → 审批档（声明式 approval）；敏感路径红线 approved 也拒。
"""

from qi_agent.tools.builtin.patch import patch
from qi_agent.tools.registry import execute_tool, get_tool


# ── 匹配策略 ─────────────────────────────────────────────────────────────


def test_exact_match(tmp_path) -> None:
    """策略 1 exact：精确替换 + diff 展示。"""
    f = tmp_path / "a.py"
    f.write_text("x = 1\ny = 2\n")
    result = patch(str(f), "y = 2", "y = 3", approved=True)
    assert "已修改" in result
    assert f.read_text() == "x = 1\ny = 3\n"
    assert "-y = 2" in result and "+y = 3" in result  # diff 展示


def test_line_trimmed_crlf(tmp_path) -> None:
    """策略 2 行尾容错：\r\n 文件用 \n 风格 old_string 也能匹配。"""
    f = tmp_path / "b.txt"
    f.write_bytes(b"line1\r\nline2\r\n")
    result = patch(str(f), "line2", "changed", approved=True)
    assert "已修改" in result
    assert f.read_bytes() == b"line1\r\nchanged\r\n"


def test_whitespace_normalized(tmp_path) -> None:
    """策略 3 空白归一：old_string 双空格 vs 文件单空格可匹配。"""
    f = tmp_path / "c.py"
    f.write_text("def f(a,  b):\n    return a\n")  # 双空格参数
    result = patch(str(f), "def f(a, b):", "def f(a, b, c):", approved=True)
    assert "已修改" in result
    assert "def f(a, b, c):" in f.read_text()


def test_indentation_flexible(tmp_path) -> None:
    """策略 4 缩进对齐：old_string 缩进漂移可匹配。"""
    f = tmp_path / "d.py"
    f.write_text("if x:\n    do_a()\n    do_b()\n")
    result = patch(str(f), "do_a()\n    do_b()", "do_a()\n    do_b()\n    do_c()", approved=True)
    assert "已修改" in result
    assert "do_c()" in f.read_text()


# ── 原子性与错误 ─────────────────────────────────────────────────────────


def test_no_match_returns_actionable_error(tmp_path) -> None:
    """匹配失败 → 可行动错误 + 文件零修改（原子性）。"""
    f = tmp_path / "e.py"
    f.write_text("keep this\n")
    result = patch(str(f), "不存在的文本", "替换", approved=True)
    assert "未找到" in result
    assert "keep this" in f.read_text()  # 没改


def test_replace_all(tmp_path) -> None:
    """replace_all=True → 全部匹配替换；False → 只第一处。"""
    f = tmp_path / "f.py"
    f.write_text("a=1\na=2\n")
    patch(str(f), "a=", "b=", replace_all=True, approved=True)
    assert f.read_text() == "b=1\nb=2\n"
    f.write_text("a=1\na=2\n")
    patch(str(f), "a=", "b=", approved=True)
    assert f.read_text() == "b=1\na=2\n"


def test_missing_file_error(tmp_path) -> None:
    """文件不存在 → 可行动错误。"""
    result = patch(str(tmp_path / "no.txt"), "x", "y", approved=True)
    assert "不存在" in result


# ── 安全 ─────────────────────────────────────────────────────────────────


def test_sensitive_path_redline(tmp_path) -> None:
    """敏感路径（.env）→ 红线拦截（approved 也拒，工具层兜底）。"""
    f = tmp_path / ".env"
    f.write_text("KEY=secret\n")
    result = patch(str(f), "KEY=secret", "KEY=new", approved=True)
    assert "拦截" in result or "拒绝" in result
    assert f.read_text() == "KEY=secret\n"  # 没改


def test_requires_approval_no_approved(tmp_path) -> None:
    """编辑已有文件但无 approved（模型路径）→ 拒绝（审批语义）。"""
    f = tmp_path / "g.py"
    f.write_text("x = 1\n")
    result = patch(str(f), "x = 1", "x = 2")
    assert "审批" in result or "拒绝" in result
    assert f.read_text() == "x = 1\n"  # 没改


def test_approved_works(tmp_path) -> None:
    """approved=True（审批注入后）→ 编辑成功。"""
    f = tmp_path / "h.py"
    f.write_text("x = 1\n")
    result = patch(str(f), "x = 1", "x = 2", approved=True)
    assert "已修改" in result
    assert f.read_text() == "x = 2\n"


def test_execute_tool_internal_guard(tmp_path) -> None:
    """execute_tool 路径：approved 需 internal 注入（防绕过）。"""
    f = tmp_path / "i.py"
    f.write_text("x = 1\n")
    # 模型路径（无 internal）→ approved 被参数校验拒绝
    r1 = execute_tool("patch", {"path": str(f), "old_string": "x = 1",
                                "new_string": "x = 2", "approved": True})
    assert "参数错误" in r1
    assert f.read_text() == "x = 1\n"
    # 审批路径（internal）→ 编辑成功
    r2 = execute_tool("patch", {"path": str(f), "old_string": "x = 1",
                                "new_string": "x = 2", "approved": True},
                      internal={"approved"})
    assert "已修改" in r2
    assert f.read_text() == "x = 2\n"


# ── 注册与审批声明 ───────────────────────────────────────────────────────


def test_patch_registered_with_approval() -> None:
    """patch 注册：审批声明 = 无条件模板（编辑已有文件 = 覆盖语义）。"""
    entry = get_tool("patch")
    assert entry is not None
    assert entry.approval == "patch 编辑 {path}"
