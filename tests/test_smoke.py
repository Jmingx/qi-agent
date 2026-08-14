"""冒烟测试：验证包可正常导入、版本号正确。"""


def test_package_imports() -> None:
    """验证 qi_agent 包可导入且版本号符合预期。"""
    import qi_agent

    assert qi_agent.__version__ == "0.1.0"
