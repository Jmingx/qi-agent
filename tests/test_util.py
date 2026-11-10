"""统一 ID 生成测试（2026-08-29——generate_id util）。"""

from qi_agent.util import generate_id


def test_format() -> None:
    """格式：<prefix>_<YYYYMMDD>_<HHMMSS>_<uuid6>。"""
    ctx = generate_id("ctx")
    parts = ctx.split("_")
    assert parts[0] == "ctx"
    assert len(parts[1]) == 8   # YYYYMMDD
    assert len(parts[2]) == 6   # HHMMSS
    assert len(parts[3]) == 6   # uuid6


def test_prefix_variants() -> None:
    """不同前缀（msg/agt/ctx——统一规则）。"""
    for prefix in ("msg", "agt", "ctx"):
        _id = generate_id(prefix)
        assert _id.startswith(prefix + "_")
        assert len(_id.split("_")) == 4


def test_uniqueness() -> None:
    """同秒多次生成不冲突（uuid 段防撞）。"""
    ids = {generate_id("msg") for _ in range(100)}
    assert len(ids) == 100
