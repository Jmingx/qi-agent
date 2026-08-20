"""测评 runner 测试：规则判定 + 任务集合法性 + 报告格式 + 超时保护。

方案：docs/plans/2026-08-20-测评系统阶段A方案.md（阶段 A 最小回归基线）
注意：判定逻辑测试不调真实 LLM（构造 history 模拟）——评测系统自身的
正确性不依赖 API。
"""

import time
from unittest import mock

from evaluation.runner import judge, run_eval
from evaluation.report import format_report
from evaluation.tasks import EvalTask, TASKS


def _history(assistant_msgs: list[str], tools: list[str] | None = None,
             blocked: bool = False) -> list[dict]:
    """构造模拟 agent 历史（assistant 消息 + 可选工具调用/拦截）。

    注意：tool_calls 用 OpenAI API 格式（对齐 agent.history 真实结构）：
    {"id", "type": "function", "function": {"name", "arguments"}}
    """
    history = []
    for i, content in enumerate(assistant_msgs):
        msg: dict = {"role": "assistant", "content": content}
        if tools and i == len(assistant_msgs) - 1:
            msg["tool_calls"] = [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": t, "arguments": "{}"}}
                for t in tools
            ]
        history.append(msg)
        if tools and i == len(assistant_msgs) - 1:
            for t in tools:
                history.append({
                    "role": "tool",
                    "tool_call_id": f"c{i}",
                    "content": "[安全拦截] 已拒绝执行" if blocked else "ok",
                })
    return history


def test_judge_all_pass() -> None:
    """全部期望满足 → 通过。"""
    task = EvalTask(
        id="t1", category="tool", name="读文件",
        steps=["读 README"], expected_tools=["read_file"],
        expected_keywords=["内容"],
    )
    history = _history(["这是文件内容"], tools=["read_file"])
    passed, failures = judge(task, history)
    assert passed is True
    assert failures == []


def test_judge_missing_tool() -> None:
    """期望工具没调用 → 失败 + 原因。"""
    task = EvalTask(
        id="t1", category="tool", name="读文件",
        steps=["读 README"], expected_tools=["read_file"],
        expected_keywords=[],
    )
    history = _history(["我没工具"], tools=["get_time"])
    passed, failures = judge(task, history)
    assert passed is False
    assert any("read_file" in f for f in failures)


def test_judge_blocked_not_triggered() -> None:
    """期望拦截没触发 → 失败。"""
    task = EvalTask(
        id="s1", category="security", name="危险命令",
        steps=["删除文件"], expected_tools=["shell"],
        expected_keywords=[], expect_blocked=True,
    )
    history = _history(["已删除"], tools=["shell"], blocked=False)
    passed, failures = judge(task, history)
    assert passed is False
    assert any("拦截" in f for f in failures)


def test_judge_keyword_missing() -> None:
    """回答缺全部关键词 → 失败（OR 语义：任一命中即满足）。"""
    task = EvalTask(
        id="c1", category="context", name="记忆",
        steps=["我叫张三"], expected_tools=[],
        expected_keywords=["张三", "李四"],
    )
    history = _history(["你好"])
    passed, failures = judge(task, history)
    assert passed is False
    assert any("关键词" in f for f in failures)


def test_judge_keyword_or_semantics() -> None:
    """OR 语义：任一关键词命中即满足（模型用词多样，AND 会误杀）。"""
    task = EvalTask(
        id="s1", category="security", name="拦截",
        steps=["删文件"], expected_tools=[],
        expected_keywords=["拒绝", "拦截", "禁止"],
    )
    # 只命中"禁止"（其他关键词都不在）→ 应通过
    history = _history(["这个操作被沙箱禁止了"])
    passed, failures = judge(task, history)
    assert passed is True
    assert failures == []


def test_judge_context_multi_step() -> None:
    """多步对话（context 类）判定正确：只看最终回答。"""
    task = EvalTask(
        id="c1", category="context", name="记忆",
        steps=["我叫张三", "我叫什么？"], expected_tools=[],
        expected_keywords=["张三"],
    )
    history = _history(["好的", "你叫张三"])
    passed, _ = judge(task, history)
    assert passed is True


def test_tasks_all_valid() -> None:
    """任务集合法性：id 唯一/类别合法/期望非空。"""
    ids = [t.id for t in TASKS]
    assert len(ids) == len(set(ids)), "任务 id 必须唯一"
    categories = {"tool", "error", "security", "context"}
    for t in TASKS:
        assert t.category in categories, f"{t.id} 类别非法"
        assert t.steps, f"{t.id} 必须至少一步"
        assert t.expected_tools or t.expected_keywords or t.expect_blocked, \
            f"{t.id} 期望为空（无判定依据）"


def test_report_format() -> None:
    """报告应包含通过率/失败明细。"""
    results = [
        {"id": "t1", "name": "读文件", "passed": True, "failures": [],
         "category": "tool", "turns": 2, "elapsed": 3.0},
        {"id": "s1", "name": "危险命令", "passed": False,
         "failures": ["未触发安全拦截"], "category": "security",
         "turns": 1, "elapsed": 2.0},
    ]
    text = format_report(results)
    assert "50.0%" in text
    assert "s1" in text
    assert "未触发安全拦截" in text


def test_task_timeout_failure(monkeypatch) -> None:
    """单任务超时 → 标记失败，不拖垮整体评测（用户评审 v2：异步+超时）。"""
    class SlowAgent:
        """假 agent：chat 卡住（模拟 LLM 挂起/工具死循环）。"""

        def __init__(self) -> None:
            self.history = []
            self._turn = 0

        def chat(self, step: str) -> str:
            time.sleep(5)  # 远超任务超时
            return "ok"

    # patch 使用点（evaluation.runner 里 from ... import build_agent 绑定）
    with mock.patch(
        "evaluation.runner.build_agent", return_value=(SlowAgent(), [])
    ):
        task = EvalTask(
            id="t9", category="tool", name="卡死任务",
            steps=["x"], timeout=0.3,
        )
        results = run_eval([task])
    assert results[0]["passed"] is False
    assert "超时" in results[0]["failures"][0]
