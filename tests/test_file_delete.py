"""file_delete 工具测试：删除文件（破坏性 → 审批 + 敏感路径红线）。

边界：只删文件不删目录（目录操作用 shell）；敏感路径永不删（红线，
approved 也拒——对齐 write_file 四档）；审批链路由 security_guard 判档。
"""

import os

from qi_agent.tools.file_delete import file_delete
from qi_agent.tools.registry import execute_tool


def test_delete_requires_approval(tmp_path) -> None:
    """无 approved（模型路径）→ 拒绝删除。"""
    f = tmp_path / "x.txt"
    f.write_text("data")
    result = file_delete(str(f))
    assert "拒绝" in result or "审批" in result
    assert f.exists()  # 没删


def test_delete_approved_works(tmp_path) -> None:
    """approved=True（审批注入后）→ 删除成功。"""
    f = tmp_path / "x.txt"
    f.write_text("data")
    result = file_delete(str(f), approved=True)
    assert "已删除" in result
    assert not f.exists()


def test_delete_sensitive_redline_even_approved(tmp_path) -> None:
    """敏感路径（.env）红线：approved 也拒（审批管不到红线）。"""
    f = tmp_path / ".env"
    f.write_text("KEY=secret")
    result = file_delete(str(f), approved=True)
    assert "拦截" in result or "拒绝" in result
    assert f.exists()  # 没删


def test_delete_nonexistent(tmp_path) -> None:
    """文件不存在 → 可行动错误。"""
    result = file_delete(str(tmp_path / "no.txt"), approved=True)
    assert "不存在" in result or "错误" in result


def test_delete_directory_rejected(tmp_path) -> None:
    """目录不能删（边界：只删文件，目录用 shell）。"""
    d = tmp_path / "subdir"
    d.mkdir()
    result = file_delete(str(d), approved=True)
    assert "目录" in result
    assert d.exists()


def test_delete_via_execute_tool_internal() -> None:
    """execute_tool 路径：approved 需 internal 注入（防绕过）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "y.txt")
        open(f, "w").write("data")
        # 模型路径（无 internal）→ approved 被参数校验拒绝
        r1 = execute_tool("file_delete", {"path": f, "approved": True})
        assert "参数错误" in r1
        assert os.path.exists(f)
        # 审批路径（internal）→ 删除成功
        r2 = execute_tool("file_delete", {"path": f, "approved": True},
                          internal={"approved"})
        assert "已删除" in r2
        assert not os.path.exists(f)
