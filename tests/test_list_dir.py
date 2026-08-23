"""list_dir 工具测试：结构化列目录（只读，边界：只列不读）。"""

import os

from qi_agent.tools.list_dir import list_dir


def test_list_dir_shows_files_and_dirs(tmp_path) -> None:
    """列出目录内容（文件/子目录/大小/类型）。"""
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("x = 1")

    result = list_dir(str(tmp_path))
    assert "a.txt" in result
    assert "sub" in result
    assert "文件" in result or "dir" in result or "目录" in result


def test_list_dir_nonexistent_path() -> None:
    """不存在的路径 → 可行动错误。"""
    result = list_dir("Z:/no_such_dir_xyz")
    assert "不存在" in result or "错误" in result


def test_list_dir_default_current_dir() -> None:
    """默认参数（path='.'）可用。"""
    result = list_dir()
    assert isinstance(result, str) and len(result) > 0


def test_list_dir_hidden_sensitive_excluded() -> None:
    """敏感目录（.git/.env 等）不列出（避免模型看到敏感路径）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".git"))
        (os.path.join(tmp, "normal.txt") and None)
        open(os.path.join(tmp, "normal.txt"), "w").close()
        result = list_dir(tmp)
    assert ".git" not in result  # 敏感目录被过滤
    assert "normal.txt" in result
