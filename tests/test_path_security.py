"""路径安全检查测试：敏感路径拦截 + 路径规范化防绕过。"""

from qi_agent.tools.path_security import is_sensitive_path


def test_sensitive_env_file() -> None:
    """.env 应被识别为敏感。"""
    assert is_sensitive_path(".env")


def test_sensitive_relative_dotenv() -> None:
    """./.env 应被识别为敏感。"""
    assert is_sensitive_path("./.env")


def test_sensitive_parent_escape() -> None:
    """../.env 应被识别为敏感（路径规范化防绕过）。"""
    assert is_sensitive_path("../.env")


def test_sensitive_absolute_env() -> None:
    """绝对路径的 .env 应被识别为敏感。"""
    assert is_sensitive_path("C:/Users/xie/project/.env")


def test_sensitive_ssh_dir() -> None:
    """.ssh 目录下的文件应被拦截。"""
    assert is_sensitive_path("C:/Users/xie/.ssh/id_rsa")
    assert is_sensitive_path("~/.ssh/known_hosts")


def test_sensitive_git_dir() -> None:
    """.git 目录下文件应被拦截。"""
    assert is_sensitive_path("project/.git/config")
    assert is_sensitive_path(".git/HEAD")


def test_sensitive_key_extension() -> None:
    """密钥扩展名文件应被拦截。"""
    assert is_sensitive_path("server.key")
    assert is_sensitive_path("certs/app.pem")


def test_normal_file_allowed() -> None:
    """普通文件应放行。"""
    assert not is_sensitive_path("README.md")
    assert not is_sensitive_path("docs/principles/01-test.md")


def test_normal_path_with_gitlike_name() -> None:
    """普通目录名含 git（如 .gitignore）应放行（只拦目录段 .git）。"""
    assert not is_sensitive_path("project/.gitignore")


def test_sensitive_env_example_allowed() -> None:
    """.env.example 应放行（不含密钥，是模板）。"""
    assert not is_sensitive_path(".env.example")


def test_read_file_blocks_env() -> None:
    """集成：read_file 读 .env 应返回安全拦截。"""
    from qi_agent.tools.read_file import read_file

    result = read_file(".env")
    assert "安全拦截" in result


def test_read_file_allows_normal() -> None:
    """集成：read_file 读普通文件应正常。"""
    from qi_agent.tools.read_file import read_file

    result = read_file("README.md")
    assert "安全拦截" not in result
