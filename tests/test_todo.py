"""todo 任务清单工具测试：CRUD + 状态机 + id 管理。

设计（方案 2026-08-22-工具三件套）：单工具 + action 参数；TodoStore
内存会话级；状态机 pending → in_progress → completed（completed 终态）；
id 自增不重用；返回操作后全量列表。
"""

import pytest

from qi_agent.tools.todo import TodoStore, _reset_store, todo


@pytest.fixture(autouse=True)
def _clean_store():
    """每个测试后重置 store（避免测试间污染）。"""
    yield
    _reset_store()


# ── TodoStore 内部状态机 ─────────────────────────────────────────────────


def test_store_crud() -> None:
    """create/list/update/complete/delete 全路径。"""
    store = TodoStore()
    item = store.create("写方案")
    assert item["id"] == 1
    assert item["status"] == "pending"

    store.create("实现代码")
    items = store.list_items()
    assert len(items) == 2
    assert [i["title"] for i in items] == ["写方案", "实现代码"]

    store.update(1, status="in_progress")
    assert store.list_items()[0]["status"] == "in_progress"

    store.complete(1)
    assert store.list_items()[0]["status"] == "completed"

    store.delete(2)
    assert len(store.list_items()) == 1


def test_store_id_not_reused() -> None:
    """id 自增不重用：删除后新建 id 继续增长。"""
    store = TodoStore()
    store.create("a")
    store.create("b")
    store.delete(1)
    item = store.create("c")
    assert item["id"] == 3  # 不重用 1


def test_store_completed_is_terminal() -> None:
    """completed 是终态：不可改回 pending/in_progress（防状态回退）。"""
    store = TodoStore()
    store.create("任务")
    store.complete(1)
    with pytest.raises(ValueError):
        store.update(1, status="pending")


def test_store_invalid_status_rejected() -> None:
    """非法状态值拒绝（白名单校验）。"""
    store = TodoStore()
    store.create("任务")
    with pytest.raises(ValueError):
        store.update(1, status="done")


def test_store_missing_id_raises() -> None:
    """不存在的 id → ValueError（可行动错误由工具层转提示）。"""
    store = TodoStore()
    with pytest.raises(ValueError):
        store.complete(99)
    with pytest.raises(ValueError):
        store.delete(99)


def test_store_empty_title_rejected() -> None:
    """空 title 拒绝。"""
    store = TodoStore()
    with pytest.raises(ValueError):
        store.create("   ")


def test_store_concurrent_create_thread_safe() -> None:
    """并行 create 线程安全（v0.4.26 修复）：20 线程并发创建无 id 竞争。"""
    from concurrent.futures import ThreadPoolExecutor

    store = TodoStore()
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(store.create, f"任务{i}") for i in range(20)]
        items = [f.result() for f in futures]
    ids = [item["id"] for item in items]
    assert len(set(ids)) == 20  # 无重复 id（无竞争）
    assert sorted(ids) == list(range(1, 21))  # 1-20 完整分配
    assert len(store.list_items()) == 20


# ── 工具层（action 分发 + 错误转提示） ────────────────────────────────────


def test_todo_create_and_list() -> None:
    """工具路径：create → 全量列表；list → 全量列表。"""
    result = todo(action="create", title="写测试")
    assert "#1" in result and "写测试" in result and "pending" in result
    result = todo(action="list")
    assert "写测试" in result


def test_todo_update_and_complete() -> None:
    """update 改状态 + complete 终态。"""
    todo(action="create", title="任务A")
    result = todo(action="update", id=1, status="in_progress")
    assert "in_progress" in result
    result = todo(action="complete", id=1)
    assert "completed" in result


def test_todo_invalid_action() -> None:
    """非法 action → 可行动错误。"""
    result = todo(action="fly")
    assert "action" in result or "错误" in result


def test_todo_missing_id_error() -> None:
    """update/complete/delete 不存在的 id → 可行动错误（不崩溃）。"""
    todo(action="create", title="x")
    result = todo(action="complete", id=99)
    assert "不存在" in result


def test_todo_registered_no_approval() -> None:
    """todo 纯内存无副作用 → 不声明审批（approval=None，放行）。"""
    from qi_agent.tools.registry import get_tool

    entry = get_tool("todo")
    assert entry is not None
    assert entry.approval is None
