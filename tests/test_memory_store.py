"""§ 分隔记忆测试（方案 2026-08-26：对齐 Hermes MemoryStore——§ 条目分隔）。

验证：MEMORY.md（长期知识）+ USER.md（用户画像）条目读写 + 去重 + 上限。
"""

import pytest

from qi_agent.storage.memory_store import MemoryStore


@pytest.fixture()
def store(tmp_path) -> MemoryStore:
    """临时记忆目录（不写真实 ~/.qi-agent）。"""
    return MemoryStore(dir_path=str(tmp_path))


def test_add_and_read_entry(store: MemoryStore) -> None:
    """追加条目 + 读取。"""
    store.add_memory("qi-agent 是 Python agent 框架")
    content = store.read_memory()
    assert "qi-agent 是 Python agent 框架" in content


def test_add_deduplicates(store: MemoryStore) -> None:
    """重复条目不写（去重——Hermes dict.fromkeys 同款）。"""
    store.add_memory("用户喜欢简洁回答")
    store.add_memory("用户喜欢简洁回答")  # 重复
    entries = store.list_entries()
    assert len(entries) == 1


def test_multiple_entries(store: MemoryStore) -> None:
    """多条目并存（§ 分隔）。"""
    store.add_memory("qi-agent 是 Python agent 框架")
    store.add_memory("用户喜欢简洁回答")
    entries = store.list_entries()
    assert len(entries) == 2
    assert "qi-agent 是 Python agent 框架" in entries
    assert "用户喜欢简洁回答" in entries


def test_user_md_separate(store: MemoryStore) -> None:
    """USER.md 独立（用户画像）。"""
    store.add_memory("用户叫小明", target="user")
    # MEMORY.md 不含用户画像
    memory = store._read_file(store.memory_path)
    assert "小明" not in memory
    # read_memory 合并两者
    content = store.read_memory()
    assert "小明" in content


def test_remove_entry(store: MemoryStore) -> None:
    """删除条目。"""
    store.add_memory("临时条目")
    store.remove_memory("临时条目")
    assert store.list_entries() == []


def test_empty_memory(store: MemoryStore) -> None:
    """无记忆 → 空内容（不报错）。"""
    assert store.read_memory() == ""


def test_char_limit(store: MemoryStore) -> None:
    """字符上限（Hermes 同款——防无限膨胀）。"""
    from qi_agent.storage.memory_store import MEMORY_CHAR_LIMIT

    long_text = "长" * 5000
    store.add_memory(long_text)
    content = store.read_memory()
    assert len(content) <= MEMORY_CHAR_LIMIT + 10  # 截断容差
