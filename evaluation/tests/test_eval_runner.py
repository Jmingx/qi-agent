"""测评 runner 测试：规则判定 + 任务集合法性 + 报告格式 + 超时保护 + 基线对比。

方案：docs/plans/2026-08-20-测评系统阶段A方案.md + 回归基线对比方案
注意：判定逻辑测试不调真实 LLM（构造 history 模拟）——评测系统自身的
正确性不依赖 API。
"""

import json
import time
from unittest import mock

from evaluation.runner import judge, run_eval
from evaluation.report import (
    compare,
    format_compare,
    format_report,
    load_report,
    save_report,
)
from evaluation.tasks import EvalTask, TASKS


def _history(assistant_msgs: list[str], tools: list[str] | None = None,
             blocked: bool = False,
             tool_actions: dict[str, str] | None = None) -> list[dict]:
    """构造模拟 agent 历史（assistant 消息 + 可选工具调用/拦截）。

    注意：tool_calls 用 OpenAI API 格式（对齐 agent.history 真实结构）：
    {"id", "type": "function", "function": {"name", "arguments"}}
    tool_actions：工具名 → action 值（写入 arguments，如 todo 的 create/list）
    """
    history = []
    tool_actions = tool_actions or {}
    for i, content in enumerate(assistant_msgs):
        msg: dict = {"role": "assistant", "content": content}
        if tools and i == len(assistant_msgs) - 1:
            msg["tool_calls"] = [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": t,
                              "arguments": "{}" if t not in tool_actions
                              else f'{{"action": "{tool_actions[t]}"}}'}}
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


# ── 阶段 C 收尾：任务级配置覆盖 + setup 前置（方案 2026-08-23）───────────


def test_evltask_new_fields_default_none() -> None:
    """新字段默认 None（现有任务构造零改动）。"""
    t = EvalTask("x", "context", "t", ["s"])
    assert t.plugin_overrides is None
    assert t.setup is None


def test_evltask_new_fields_custom() -> None:
    """plugin_overrides + setup 可赋值。"""
    called = []

    def setup():
        called.append(1)

    t = EvalTask(
        "c-long-1", "context", "长对话", ["s"],
        plugin_overrides={"context_manager": {"compress": {"window": 2000}}},
        setup=setup,
    )
    assert t.plugin_overrides["context_manager"]["compress"]["window"] == 2000
    t.setup()
    assert called == [1]


def test_runner_passes_overrides_and_setup() -> None:
    """runner 传 plugin_overrides 给 build_agent + 执行 setup 前置。

    阶段 C 收尾：L3 任务用小窗口覆盖触发压缩 + setup 注入 sticky。
    """
    seen: dict = {}
    setup_ran: list[str] = []

    def setup():
        setup_ran.append("setup-ran")

    def fake_build_agent(**kwargs):
        seen.update(kwargs)
        return type("B", (), {"agent": FakeAgent(), "manager": None,
                              "agent_id": "fake", "installed": []})()

    class FakeAgent:
        def __init__(self) -> None:
            self.history = []
            self._turn = 0
            self.context = type("C", (), {"phase": type("P", (), {"value": "idle"})(),
                                          "turn": 0, "usage": {},
                                          "messages": []})()

        def chat(self, step: str) -> str:
            # 模拟：事实在对话中保持（压缩后依然答对）
            self._turn += 1  # 对齐真实 agent：每句用户话 +1（累计值）
            reply = "好的，你的猫叫咪咪" if "猫" in step else "好的"
            self.history.append({"role": "assistant", "content": reply})
            return reply

    with mock.patch("evaluation.runner.build_agent",
                    side_effect=fake_build_agent):
        task = EvalTask(
            "c-long-1", "context", "事实保持",
            steps=["聊聊天", "我的猫叫什么"], expected_keywords=["咪咪"],
            plugin_overrides={"context_manager": {"compress": {"window": 2000}}},
            setup=setup,
        )
        results = run_eval([task])
    assert setup_ran == ["setup-ran"]  # setup 前置已执行
    assert seen["plugin_overrides"]["context_manager"]["compress"]["window"] == 2000
    assert results[0]["passed"] is True
    # turns 语义：累计轮数（2 句用户话 = 2），不是累加和（1+2=3）
    # ——2026-08-23 修复：曾把 agent._turn（累计值）逐次累加 → 多 step 任务虚高
    assert results[0]["turns"] == 2


def test_judge_forbidden_tools() -> None:
    """forbidden_tools：调用过 → 失败（L3：压缩后不重做）。"""
    task = EvalTask(
        "c-long-3", "context", "不重做", steps=["x"],
        expected_keywords=["周报"], forbidden_tools=["todo"],
    )
    history = _history(["创建了任务"], tools=["todo"])
    passed, failures = judge(task, history)
    assert passed is False
    assert any("todo" in f for f in failures)


def test_judge_forbidden_tools_clean() -> None:
    """forbidden_tools 未调用 → 通过。"""
    task = EvalTask(
        "c-long-3", "context", "不重做", steps=["x"],
        expected_keywords=["周报"], forbidden_tools=["todo"],
    )
    history = _history(["周报任务已完成"])
    passed, failures = judge(task, history)
    assert passed is True
    assert failures == []


def test_judge_forbidden_tool_action_create_blocked() -> None:
    """todo:create 粒度——实际调 create → 失败（压缩后重做已完成工作）。"""
    task = EvalTask(
        "c-long-3", "context", "不重做", steps=["x"],
        expected_keywords=["周报"], forbidden_tools=["todo:create"],
    )
    history = _history(["创建任务"], tools=["todo"], tool_actions={"todo": "create"})
    passed, failures = judge(task, history)
    assert passed is False
    assert any("create" in f for f in failures)


def test_judge_forbidden_tool_action_query_allowed() -> None:
    """todo:create 粒度——只查 list → 通过（查询是合理行为）。"""
    task = EvalTask(
        "c-long-3", "context", "不重做", steps=["x"],
        expected_keywords=["周报"], forbidden_tools=["todo:create"],
    )
    history = _history(["任务是写周报"], tools=["todo"], tool_actions={"todo": "list"})
    passed, failures = judge(task, history)
    assert passed is True
    assert failures == []


def test_judge_forbidden_tool_action_other_tool_allowed() -> None:
    """todo:create 粒度——其他工具调用不受影响。"""
    task = EvalTask(
        "c-long-3", "context", "不重做", steps=["x"],
        expected_keywords=["周报"], forbidden_tools=["todo:create"],
    )
    history = _history(["周报相关，看下时间"], tools=["get_time"])
    passed, failures = judge(task, history)
    assert passed is True
    assert failures == []


def test_judge_keyword_min_count() -> None:
    """expected_keyword_min_count=2：历史里关键词出现 ≥2 次才通过（L4 对比）。"""
    task = EvalTask(
        "c-long-4", "context", "一致性对比", steps=["x", "y"],
        expected_keywords=["咪咪"], expected_keyword_min_count=2,
    )
    # 压缩前回答 1 次 + 压缩后回答 1 次 = 2 次 → 通过
    history = _history(["你的猫叫咪咪", "猫的名字是咪咪"])
    passed, failures = judge(task, history)
    assert passed is True
    assert failures == []


def test_judge_keyword_min_count_fail() -> None:
    """计数不足（仅 1 次）→ 失败。"""
    task = EvalTask(
        "c-long-4", "context", "一致性对比", steps=["x"],
        expected_keywords=["咪咪"], expected_keyword_min_count=2,
    )
    history = _history(["猫叫咪咪"])  # 只回答一次
    passed, failures = judge(task, history)
    assert passed is False
    assert any("次数" in f for f in failures)


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
        "evaluation.runner.build_agent",
        return_value=type("B", (), {"agent": SlowAgent(), "manager": None,
                                    "agent_id": "fake", "installed": []})(),
    ):
        task = EvalTask(
            id="t9", category="tool", name="卡死任务",
            steps=["x"], timeout=0.3,
        )
        results = run_eval([task])
    assert results[0]["passed"] is False
    assert "超时" in results[0]["failures"][0]


# ── 回归基线对比（方案 v0.4.15）───────────────────────────────────────────


def _result(id_: str, passed: bool) -> dict:
    """构造单条评测结果。"""
    return {"id": id_, "name": id_, "category": "tool",
            "passed": passed, "failures": [], "turns": 1, "elapsed": 1.0}


def test_compare_regression() -> None:
    """本次❌上次✅ → regressions 含该任务 + delta 为负。"""
    prev = [_result("t1", True), _result("t2", True)]
    cur = [_result("t1", False), _result("t2", True)]
    cmp = compare(prev, cur)
    assert cmp["regressions"] == ["t1"]
    assert cmp["delta_pct"] == -50.0


def test_compare_improved() -> None:
    """本次✅上次❌ → improved 含该任务 + delta 为正。"""
    prev = [_result("t1", False)]
    cur = [_result("t1", True)]
    cmp = compare(prev, cur)
    assert cmp["improved"] == ["t1"]
    assert cmp["delta_pct"] == 100.0


def test_compare_stable_and_added() -> None:
    """相同→稳定（不进回归/改善）；新任务→added。"""
    prev = [_result("t1", True)]
    cur = [_result("t1", True), _result("t2", False)]
    cmp = compare(prev, cur)
    assert cmp["regressions"] == []
    assert cmp["improved"] == []
    assert cmp["added"] == ["t2"]


def test_compare_delta_percent() -> None:
    """通过率变化计算（15→14 = -6.2%，Python round 银行家舍入）。"""
    prev = [_result(f"t{i}", True) for i in range(15)] + [_result("t16", False)]
    cur = (
        [_result(f"t{i}", True) for i in range(14)]
        + [_result("t15", False), _result("t16", False)]
    )
    cmp = compare(prev, cur)
    assert cmp["delta_pct"] == -6.2  # 6.25 银行家舍入为 6.2（round 语义）
    assert cmp["prev_passed"] == 15
    assert cmp["cur_passed"] == 14


def test_format_compare_with_regression() -> None:
    """回归时输出应含回归明细 + 告警提示。"""
    cmp = {
        "prev_passed": 15, "cur_passed": 14, "total": 16, "delta_pct": -6.3,
        "regressions": ["s1"], "improved": ["e2"], "added": [],
    }
    text = format_compare("2026-08-20T21:58:00", cmp)
    assert "s1" in text
    assert "检测到回归" in text
    assert "▼" in text


def test_format_compare_clean() -> None:
    """无回归 → '无回归'。"""
    cmp = {
        "prev_passed": 15, "cur_passed": 15, "total": 16, "delta_pct": 0.0,
        "regressions": [], "improved": ["e2"], "added": [],
    }
    text = format_compare("2026-08-20T21:58:00", cmp)
    assert "无回归" in text
    assert "＝" in text


def test_save_load_roundtrip(tmp_path, monkeypatch) -> None:
    """save 后 load 一致（run_at + results）。"""
    import evaluation.report as report_mod

    monkeypatch.setattr(report_mod, "REPORT_JSON", tmp_path / "last_report.json")
    results = [_result("t1", True)]
    save_report(results)
    run_at, loaded = load_report()
    assert run_at is not None  # 时间戳已记录
    assert loaded == results


def test_load_legacy_format(tmp_path, monkeypatch) -> None:
    """旧格式（纯 list，无 run_at）能读取（向后兼容）。"""
    import evaluation.report as report_mod

    p = tmp_path / "last_report.json"
    p.write_text(json.dumps([_result("t1", True)]), encoding="utf-8")
    monkeypatch.setattr(report_mod, "REPORT_JSON", p)
    run_at, loaded = load_report()
    assert run_at is None  # 旧格式无时间戳
    assert loaded[0]["id"] == "t1"


# ── LLM 调用异常兜底（v0.4.24，配套 LLM timeout）────────────────────────


def test_task_llm_error_fails_gracefully() -> None:
    """任务内 LLM 抛异常（超时/网络）→ 任务失败，不拖垮整体评测。

    v0.4.24 配套：LLMClient 加 timeout 后，挂起的 LLM 调用最多 timeout 秒
    抛 APITimeoutError——runner 若只捕获 wait_for 超时，异常会冒泡让
    asyncio.run 整体崩溃；必须兜底为单任务失败。
    """

    class ErrorAgent:
        """假 agent：chat 抛异常（模拟 LLM 超时/网络错误）。"""

        def __init__(self) -> None:
            self.history = []
            self._turn = 0

        def chat(self, step: str) -> str:
            raise TimeoutError("LLM 调用超时（60s）")

    with mock.patch(
        "evaluation.runner.build_agent",
        return_value=type("B", (), {"agent": ErrorAgent(), "manager": None,
                                    "agent_id": "fake", "installed": []})(),
    ):
        task = EvalTask(
            id="t10", category="tool", name="LLM异常任务", steps=["x"],
        )
        results = run_eval([task])
    assert results[0]["passed"] is False
    assert "LLM 调用超时" in results[0]["failures"][0]
