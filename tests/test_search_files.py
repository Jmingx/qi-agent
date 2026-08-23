"""search_files 工具测试：文件内容搜索（只读，边界：只定位不读全文）。

实现为纯 Python（os.walk + 正则）——不依赖 rg 二进制（跨平台，对齐
零新依赖哲学）。返回 文件+行号+匹配行（截断）。
"""

from qi_agent.tools.builtin.search_files import search_files


def test_search_finds_matches(tmp_path) -> None:
    """内容搜索命中 → 返回 文件+行号+匹配行。"""
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = foo()\n", encoding="utf-8")

    result = search_files("foo", path=str(tmp_path))
    assert "a.py" in result
    assert "b.py" in result
    assert "1" in result  # 行号


def test_search_no_match(tmp_path) -> None:
    """无匹配 → 明确提示（模型知道可换关键词）。"""
    (tmp_path / "a.py").write_text("nothing here", encoding="utf-8")
    result = search_files("zzz_nomatch", path=str(tmp_path))
    assert "无匹配" in result or "未找到" in result


def test_search_file_glob_filter(tmp_path) -> None:
    """file_glob 过滤文件类型。"""
    (tmp_path / "a.py").write_text("target_xyz\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("target_xyz\n", encoding="utf-8")

    result = search_files("target_xyz", path=str(tmp_path), file_glob="*.py")
    assert "a.py" in result
    assert "a.txt" not in result  # 被 glob 过滤


def test_search_nonexistent_path() -> None:
    """不存在的目录 → 可行动错误。"""
    result = search_files("x", path="Z:/no_such_dir_xyz")
    assert "不存在" in result or "错误" in result


def test_search_skips_hidden_and_venv(tmp_path) -> None:
    """跳过敏感/大目录（.git/.venv/node_modules）——防噪音。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("target_xyz\n", encoding="utf-8")
    (tmp_path / "normal.py").write_text("target_xyz\n", encoding="utf-8")

    result = search_files("target_xyz", path=str(tmp_path))
    assert "normal.py" in result
    assert ".git" not in result  # 敏感目录跳过
