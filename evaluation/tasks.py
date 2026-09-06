"""旧 EvalTask API 的兼容层；任务数据只从 evaluation/suites JSONL 加载。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evaluation.case_models import EvalCase
from evaluation.suite_loader import load_suites


@dataclass
class EvalTask:
    """迁移期兼容类型，字段与 JSONL EvalCase 一一对应。"""

    id: str
    category: str
    name: str
    steps: list[str]
    expected_tools: list[str] = field(default_factory=list)
    expected_tools_any: list[str] = field(default_factory=list)
    expected_keywords: list[str] = field(default_factory=list)
    expect_blocked: bool = False
    timeout: float = 60.0
    plugin_overrides: dict[str, Any] | None = None
    forbidden_tools: list[str] = field(default_factory=list)
    expected_keyword_min_count: int = 1
    expected_memory: str | None = None
    memory_target: str = "memory"
    expected_rubric: str | None = None
    preconditions: dict[str, Any] | None = None
    # 仅为旧单元测试/调用方保留字段；JSONL runner 不执行 Python hook。
    setup: Any = None

    @classmethod
    def from_case(cls, case: EvalCase) -> "EvalTask":
        return cls(
            id=case.case_id, category=case.category, name=case.name,
            steps=list(case.conversation_steps()),
            expected_tools=list(case.expected_tools or case.must_use_tools),
            expected_tools_any=list(case.expected_tools_any),
            expected_keywords=list(case.expected_keywords or case.reply_contains_any),
            expect_blocked=case.expect_blocked, timeout=case.timeout,
            plugin_overrides=case.plugin_overrides,
            forbidden_tools=list(case.forbidden_tools or case.must_not_use_tools),
            expected_keyword_min_count=case.expected_keyword_min_count,
            expected_memory=case.expected_memory, memory_target=case.memory_target,
            expected_rubric=case.expected_rubric, preconditions=case.preconditions,
        )


def _tasks(*suites: str) -> list[EvalTask]:
    return [EvalTask.from_case(case) for case in load_suites(suites)]


# 这些名称仅为旧脚本/测试保留，任何新 runner 都直接使用 suite_loader。
TASKS = _tasks("regression")
LONG_TASKS = _tasks("long_context")
SUBAGENT_TASKS = _tasks("subagent")
SMOKE_TASKS = _tasks("smoke")
