"""测评打分系统测试（方案 2026-08-29——全部 mock，不调真实 API）。

Phase 1：指标采集（token/成本）
Phase 2：打分（judge 质量分 + 规则短路）
Phase 3：历史保留（eval_runs/ + runs_index + 趋势 + 归因）
"""

import pytest

from evaluation import history
from evaluation.history import (
    load_run,
    load_runs,
    save_run,
    task_trend,
    trends,
    window_regression,
)
from evaluation.report import format_report
from evaluation.runner import estimate_cost, judge, score_task
from evaluation.tasks import EvalTask


@pytest.fixture()
def tmp_history(tmp_path, monkeypatch):
    """隔离历史目录（不污染真实 eval_runs/）。"""
    monkeypatch.setattr(history, "EVAL_RUNS_DIR", tmp_path)
    monkeypatch.setattr(history, "INDEX_FILE", tmp_path / "runs_index.jsonl")
    return tmp_path


# ── Phase 1：指标采集 ──────────────────────────────────────────────────
class TestJudgeToolsAny:
    """expected_tools_any（任一即可）语义测试（2026-08-31 修正）。"""

    def test_any_tool_satisfied(self):
        """任一期望工具命中 → 通过。"""
        task = EvalTask("x", "tool", "侦察", ["hi"],
                        expected_tools_any=["read_file", "list_dir"])
        history = [{"role": "assistant", "content": "ok",
                    "tool_calls": [{"function": {"name": "list_dir"}}]}]
        passed, failures = judge(task, history)
        assert passed, failures

    def test_any_tool_missing(self):
        """无一命中 → 失败。"""
        task = EvalTask("x", "tool", "侦察", ["hi"],
                        expected_tools_any=["read_file", "list_dir"])
        history = [{"role": "assistant", "content": "ok",
                    "tool_calls": [{"function": {"name": "shell"}}]}]
        passed, failures = judge(task, history)
        assert not passed
        assert "任一期望工具" in failures[0]

    def test_and_semantics_unchanged(self):
        """expected_tools 仍是 AND（全部必须调用）。"""
        task = EvalTask("x", "tool", "双工具", ["hi"],
                        expected_tools=["read_file", "list_dir"])
        history = [{"role": "assistant", "content": "ok",
                    "tool_calls": [{"function": {"name": "read_file"}}]}]
        passed, failures = judge(task, history)
        assert not passed
        assert "未调用工具 list_dir" in failures[0]
class TestEstimateCost:
    def test_cost_calculation(self):
        """成本估算：prompt × 输入单价 + completion × 输出单价。"""
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
        assert estimate_cost(usage) == pytest.approx(2.0)  # 1.0 + 1.0

    def test_cost_empty(self):
        assert estimate_cost(None) == 0.0
        assert estimate_cost({}) == 0.0


class TestReportMetrics:
    def test_report_includes_tokens_and_cost(self):
        """报告输出总 token / 总成本（Phase 1 验收）。"""
        results = [
            {"id": "a", "name": "A", "category": "c1", "passed": True,
             "failures": [], "turns": 2, "elapsed": 3.0,
             "tokens": {"total_tokens": 10_000}, "cost": 0.02},
            {"id": "b", "name": "B", "category": "c1", "passed": False,
             "failures": ["x"], "turns": 1, "elapsed": 2.0,
             "tokens": {"total_tokens": 5_000}, "cost": 0.01},
        ]
        text = format_report(results)
        assert "总 token: 15000" in text
        assert "总成本: ¥0.0300" in text

    def test_report_missing_metrics_tolerant(self):
        """旧格式 result（无 tokens/cost）不炸——容错。"""
        results = [
            {"id": "a", "name": "A", "category": "c1", "passed": True,
             "failures": [], "turns": 1, "elapsed": 1.0},
        ]
        text = format_report(results)
        assert "总 token: 0" in text
        assert "总成本: ¥0.0000" in text


# ── Phase 2：打分系统 ──────────────────────────────────────────────────
class FakeJudgeClient:
    """假 judge 客户端（测试注入——不调真实 API）。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    def chat(self, messages) -> object:
        self.calls += 1
        return type("Resp", (), {"content": self._content})()


def _task(rubric: str | None) -> EvalTask:
    return EvalTask(id="t1", category="c1", name="测试任务",
                    steps=["hi"], expected_rubric=rubric)


class TestScoreTask:
    def test_rule_failure_short_circuit_zero(self):
        """规则不过 → 0 分短路（不进 judge——省成本）。"""
        client = FakeJudgeClient('{"score": 95, "reason": "x", '
                                 '"missing_points": []}')
        task = _task("完成度标准")
        score = score_task(task, [{"role": "assistant", "content": "a"}],
                           ["未调用工具 x"], client=client)
        assert score == 0
        assert client.calls == 0  # judge 未被调用（短路）

    def test_no_rubric_no_judge(self):
        """无 rubric → None（存量任务零成本，不调 judge）。"""
        client = FakeJudgeClient("{}")
        score = score_task(_task(None), [], [], client=client)
        assert score is None
        assert client.calls == 0

    def test_judge_scores(self):
        """规则过 + rubric → judge 打分。"""
        client = FakeJudgeClient(
            '{"score": 85, "reason": "完成", '
            '"missing_points": ["细节不足"]}')
        history = [{"role": "assistant", "content": "完成",
                    "tool_calls": [{"function": {"name": "read_file"}}]}]
        score = score_task(_task("完成度标准"), history, [], client=client)
        assert score == 85
        assert client.calls == 1

    def test_judge_score_clamped(self):
        """judge 越界分数夹取 0-100。"""
        client = FakeJudgeClient('{"score": 999}')
        assert score_task(_task("r"), [], [], client=client) == 100
        client = FakeJudgeClient('{"score": -5}')
        assert score_task(_task("r"), [], [], client=client) == 0

    def test_judge_failure_returns_none(self):
        """judge 输出非法 JSON → None（不阻塞评测）。"""
        client = FakeJudgeClient("这不是JSON")
        assert score_task(_task("r"), [], [], client=client) is None

    def test_report_score_average(self):
        """报告输出质量均分。"""
        results = [
            {"id": "a", "name": "A", "category": "c1", "passed": True,
             "failures": [], "score": 90, "turns": 1, "elapsed": 1.0,
             "tokens": {}, "cost": 0.0},
            {"id": "b", "name": "B", "category": "c1", "passed": True,
             "failures": [], "score": 70, "turns": 1, "elapsed": 1.0,
             "tokens": {}, "cost": 0.0},
            {"id": "c", "name": "C", "category": "c1", "passed": True,
             "failures": [], "score": None, "turns": 1, "elapsed": 1.0,
             "tokens": {}, "cost": 0.0},
        ]
        text = format_report(results)
        assert "质量均分: 80.0（打分 2/3）" in text


# ── Phase 3：历史完整保留 + 趋势 ───────────────────────────────────────
def _results(score: int | None = 85, passed: bool = True) -> list[dict]:
    return [{
        "id": "t1", "name": "任务一", "category": "c1", "passed": passed,
        "failures": [] if passed else ["x"], "score": score,
        "turns": 2, "elapsed": 3.0,
        "tokens": {"total_tokens": 10_000}, "cost": 0.02,
    }]


class TestHistory:
    def test_save_run_creates_file_and_index(self, tmp_history):
        """保存：run 文件 + 索引追加（验收 1/2）。"""
        run_id = save_run(_results())
        assert (tmp_history / f"{run_id}_report.json").exists()
        runs = load_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == run_id
        assert runs[0]["pass_rate"] == 1.0
        assert runs[0]["total_tokens"] == 10_000

    def test_save_multiple_runs_append_only(self, tmp_history):
        """多次保存：历史永不覆盖（追加式索引）。"""
        save_run(_results(score=90))
        save_run(_results(score=70))
        save_run(_results(score=60, passed=False))
        runs = load_runs()
        assert len(runs) == 3  # 全部保留（不覆盖）
        assert [r["avg_score"] for r in runs] == [90.0, 70.0, 60.0]

    def test_load_run_detail(self, tmp_history):
        """读单 run 明细（完整 results）。"""
        run_id = save_run(_results())
        detail = load_run(run_id)
        assert detail is not None
        assert detail["results"][0]["id"] == "t1"
        assert load_run("nobody") is None

    def test_trends_sequence(self, tmp_history):
        """趋势序列（最近 window 次）。"""
        save_run(_results(score=90))
        save_run(_results(score=70))
        data = trends(window=5)
        assert data["avg_scores"] == [90.0, 70.0]
        assert data["total_tokens"] == [10_000, 10_000]
        assert len(data["run_ids"]) == 2

    def test_window_regression_alarm(self, tmp_history):
        """滑动窗口回归：通过率下降 → 告警（验收 5）。"""
        save_run(_results())
        save_run(_results())
        save_run(_results())
        save_run(_results(passed=False))  # 本次不过
        alarms = window_regression(window=5)
        assert len(alarms) == 1
        assert "回归" in alarms[0]

    def test_window_regression_clean(self, tmp_history):
        """无回归 → 空告警。"""
        save_run(_results())
        save_run(_results())
        assert window_regression() == []

    def test_task_trend_attribution(self, tmp_history):
        """任务归因：跨 run 的 score/passed 序列（验收 6）。"""
        save_run(_results(score=90))
        save_run(_results(score=70, passed=False))
        seq = task_trend("t1")
        assert len(seq) == 2
        assert seq[0] == {"run_id": seq[0]["run_id"],
                          "passed": True, "score": 90}
        assert seq[1]["passed"] is False and seq[1]["score"] == 70
