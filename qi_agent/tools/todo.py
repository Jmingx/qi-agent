"""todo 任务清单工具：agent 长任务自我追踪（CRUD + 状态机）。

设计（方案 2026-08-22-工具三件套）：
- 单工具 + action 参数（create/list/update/complete/delete）——显式
  CRUD 学习价值高（状态机），token 省（对比 Hermes 覆盖式写全列表）
- TodoStore 内存会话级（对齐 Hermes "one instance per AIAgent"）——
  纯内存无外部副作用 → 不审批（放行）
- 状态机：pending → in_progress → completed（completed 终态不可回退，
  防模型把已完成任务改回 pending 造成"假进度"）
- id 自增不重用；返回操作后全量列表（模型可见完整状态）
"""

from __future__ import annotations

import threading

from qi_agent.tools.registry import register

# 合法状态白名单（状态机值域）
_VALID_STATUSES = ("pending", "in_progress", "completed")


class TodoStore:
    """会话级任务清单（内存存储，每 Agent 一个实例）。

    线程安全（v0.4.26 修复）：并行工具调用下 create 的 _next_id += 1
    非原子（真实暴露：9 个并行 create 可能 id 竞争）——全部公开方法
    加锁，读-改-写原子化。
    """

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()

    def create(self, title: str) -> dict:
        """新建任务（pending 初始态），返回任务条目。"""
        title = title.strip()
        if not title:
            raise ValueError("任务内容不能为空")
        with self._lock:
            item = {"id": self._next_id, "title": title, "status": "pending"}
            self._items.append(item)
            self._next_id += 1
        return item

    def list_items(self) -> list[dict]:
        """全量任务（创建顺序，快照副本）。"""
        with self._lock:
            return list(self._items)

    def update(self, id: int, title: str | None = None,
               status: str | None = None) -> dict:
        """更新任务（title 和/或 status）。"""
        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("任务内容不能为空")
        if status is not None:
            if status not in _VALID_STATUSES:
                raise ValueError(f"非法状态: {status}（合法: {_VALID_STATUSES}）")
        with self._lock:
            item = self._find(id)
            if title is not None:
                item["title"] = title
            if status is not None:
                if item["status"] == "completed" and status != "completed":
                    raise ValueError("completed 是终态，不可回退到其他状态")
                item["status"] = status
            return dict(item)

    def complete(self, id: int) -> dict:
        """任务置为 completed（终态）。"""
        return self.update(id, status="completed")

    def delete(self, id: int) -> dict:
        """移除任务。"""
        with self._lock:
            item = self._find(id)
            self._items.remove(item)
            return dict(item)

    def _find(self, id: int) -> dict:
        """按 id 找任务（不存在抛 ValueError；须在持锁内调用）。"""
        for item in self._items:
            if item["id"] == id:
                return item
        raise ValueError(f"任务不存在: id={id}")


# 进程级单例（CLI 一次一个 agent；测试用 _reset_store 重置）
_STORE = TodoStore()


def _reset_store() -> None:
    """重置 store（测试隔离用）。"""
    global _STORE
    _STORE = TodoStore()


def todo(action: str, title: str = "", id: int = 0, status: str = "") -> str:
    """任务清单操作（单工具 + action 分发）。

    Args:
        action: create（新建）/ list（查看）/ update（改）/ complete（完成）/
            delete（删除）
        title: create 必填（任务内容）；update 可选（改标题）
        id: update/complete/delete 必填（任务 id）
        status: update 可选（pending/in_progress/completed）

    Returns:
        操作后的全量任务列表文本，或可行动错误
    """
    try:
        if action == "create":
            _STORE.create(title)
        elif action == "list":
            pass  # 直接返回全量
        elif action == "update":
            _STORE.update(id, title=title or None,
                          status=status or None)
        elif action == "complete":
            _STORE.complete(id)
        elif action == "delete":
            _STORE.delete(id)
        else:
            return (
                f"[参数错误] 未知 action: {action}"
                "（合法: create/list/update/complete/delete）"
            )
    except ValueError as exc:
        return f"[错误] {exc}"

    items = _STORE.list_items()
    if not items:
        return "[todo] 任务列表为空"
    lines = [f"[todo] 共 {len(items)} 项:"]
    for item in items:
        mark = {"pending": "⬜", "in_progress": "🚧", "completed": "✅"}[item["status"]]
        lines.append(f"  {mark} #{item['id']} [{item['status']}] {item['title']}")
    return "\n".join(lines)


register(
    name="todo",
    toolset="builtin",
    handler=todo,
    description=(
        "任务清单管理（会话级）：agent 长任务自我追踪——把大任务拆成"
        "清单逐项完成。action: create（新建）/ list（查看）/ update（改）/"
        "complete（完成）/ delete（删除）。纯内存状态，无需审批"
    ),
    # 审批声明：纯内存无副作用 → 放行（None）
    approval=None,
    # 手写 schema：id/title/status 按 action 选择性必填（全字段可选 + 工具内校验）
    schema={
        "type": "function",
        "function": {
            "name": "todo",
            "description": (
                "任务清单管理（会话级）：把长任务拆成清单逐项追踪。"
                "action 必填：create（新建，需 title）/ list（查看全部）/"
                "update（修改 id 任务，可改 title 或 status）/ complete"
                "（标记 id 任务完成）/ delete（删除 id 任务）。"
                "状态: pending（待办）/ in_progress（进行中）/ completed"
                "（已完成，终态）。返回操作后的完整清单"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "操作: create/list/update/complete/delete",
                    },
                    "title": {
                        "type": "string",
                        "description": "任务内容（create 必填；update 可改标题）",
                    },
                    "id": {
                        "type": "integer",
                        "description": "任务 id（update/complete/delete 必填）",
                    },
                    "status": {
                        "type": "string",
                        "description": "目标状态（update 用: pending/in_progress/completed）",
                    },
                },
                "required": ["action"],
            },
        },
    },
)
