"""read_file 分页测试：offset/limit 行级分页 + header 元信息 + 字符上限。

方案：docs/plans/2026-08-20-read_file分页升级方案.md（决策点 1-4 已批准）
"""

import pytest

from qi_agent.tools.read_file import read_file


@pytest.fixture()
def sample(tmp_path):
    """构造 207 行样例文件（模拟 tmp/snake.py 规模）。"""
    path = tmp_path / "sample.txt"
    path.write_text("".join(f"line {i}\n" for i in range(1, 208)), encoding="utf-8")
    return path


def test_read_file_small(tmp_path) -> None:
    """小文件（<limit 行）→ 完整内容 + header 总行数，无 tail。"""
    path = tmp_path / "small.txt"
    path.write_text("a\nb\n", encoding="utf-8")
    result = read_file(str(path))
    assert "第 1-2 行（共 2 行）" in result
    assert "line" not in result  # 内容无 tail
    assert "已截断" not in result
    assert "a\nb\n" in result


def test_read_file_large_all(sample) -> None:
    """207 行文件默认读取（limit=2000）→ 全量 + header，无 tail。"""
    result = read_file(str(sample))
    assert "第 1-207 行（共 207 行）" in result
    assert "line 1" in result
    assert "line 207" in result
    assert "已截断" not in result


def test_read_file_offset(sample) -> None:
    """offset=201 → 只返回剩余行，无 tail（读完）。"""
    result = read_file(str(sample), offset=201)
    assert "第 201-207 行（共 207 行）" in result
    assert "line 201" in result
    assert "line 207" in result
    assert "已截断" not in result
    assert "line 1" not in result


def test_read_file_limit(sample) -> None:
    """limit=100 → 100 行 + tail 续读提示（可行动）。"""
    result = read_file(str(sample), limit=100)
    assert "第 1-100 行（共 207 行）" in result
    assert "line 100" in result
    assert "line 101" not in result
    assert "剩余 107 行" in result
    assert "offset=101" in result


def test_read_file_offset_invalid(sample) -> None:
    """offset<1 → 自动修正为 1。"""
    result = read_file(str(sample), offset=0)
    assert "第 1-" in result
    result = read_file(str(sample), offset=-5)
    assert "第 1-" in result


def test_read_file_char_cap(tmp_path) -> None:
    """字符上限 50_000 双保险：超大内容截断 + 续读提示。"""
    path = tmp_path / "big.txt"
    path.write_text("x" * 60_000, encoding="utf-8")
    result = read_file(str(path))
    assert "内容过长已截断" in result
    assert "分段读取" in result


def test_read_file_blocks_env() -> None:
    """敏感路径仍拦截（回归）。"""
    result = read_file(".env")
    assert "安全拦截" in result


def test_read_file_missing() -> None:
    """文件不存在（回归）。"""
    result = read_file("no_such_file_xyz.txt")
    assert "文件不存在" in result


def test_read_file_is_dir() -> None:
    """路径是目录（回归）。"""
    result = read_file(".")
    assert "目录" in result or "错误" in result
