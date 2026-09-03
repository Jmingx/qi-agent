"""评测用例与结果的数据模型。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "deepseek-v4-flash"
INPUT_COST_PER_M = 1.0
OUTPUT_COST_PER_M = 2.0


@dataclass(frozen=True)
class EvalCase:
    """单条验证用例。"""

    case_id: str
    prompt: str
    must_use_tools: tuple[str, ...]
    must_not_use_tools: tuple[str, ...]
    reply_contains_any: tuple[str, ...]
    reply_regex: str
    max_turns: int


@dataclass
class CaseResult:
    """单条用例执行结果。"""

    case_id: str
    session_id: str
    reply: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_s: float
    estimated_cost_rmb: float
    tools_used: list[str]
    passed: bool
    failures: list[str]

    def to_experiment_payload(self) -> dict[str, Any]:
        """写入 Opik 的评测结果负载。"""

        return {
            "case_id": self.case_id,
            "session_id": self.session_id,
            "reply": self.reply,
            "turns": self.turns,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "elapsed_s": round(self.elapsed_s, 3),
            "estimated_cost_rmb": round(self.estimated_cost_rmb, 6),
            "tools_used": self.tools_used,
            "passed": self.passed,
            "failures": self.failures,
            "model": DEFAULT_MODEL,
        }

    def to_feedback_scores(self, case: EvalCase) -> list[dict[str, Any]]:
        """把规则断言转成 Opik feedback scores。"""

        score_map = {
            "smoke_pass": 1.0 if self.passed else 0.0,
            "turn_budget": 1.0 if self.turns <= case.max_turns else 0.0,
            "reply_regex": 1.0
            if not case.reply_regex or re.search(case.reply_regex, self.reply)
            else 0.0,
        }
        for required in case.must_use_tools:
            score_map[f"tool:{required}"] = 1.0 if required in self.tools_used else 0.0
        for forbidden in case.must_not_use_tools:
            score_map[f"no_tool:{forbidden}"] = 0.0 if forbidden in self.tools_used else 1.0
        reason = "; ".join(self.failures) if self.failures else "ok"
        return [
            {"name": name, "value": value, "source": "sdk", "reason": reason}
            for name, value in score_map.items()
        ]


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens / 1_000_000 * INPUT_COST_PER_M
        + completion_tokens / 1_000_000 * OUTPUT_COST_PER_M
    )


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            payload = json.loads(raw)
            cases.append(
                EvalCase(
                    case_id=str(payload["id"]),
                    prompt=str(payload["prompt"]),
                    must_use_tools=tuple(payload.get("must_use_tools", [])),
                    must_not_use_tools=tuple(payload.get("must_not_use_tools", [])),
                    reply_contains_any=tuple(payload.get("reply_contains_any", [])),
                    reply_regex=str(payload.get("reply_regex", "")),
                    max_turns=int(payload.get("max_turns", 2)),
                )
            )
    if len(cases) != 3:
        raise ValueError(f"cases.jsonl 必须正好 3 条用例，当前为 {len(cases)} 条")
    return cases


def validate_case(case: EvalCase, reply: str, turns: int, tools_used: list[str]) -> list[str]:
    failures: list[str] = []
    for required in case.must_use_tools:
        if required not in tools_used:
            failures.append(f"未调用工具 {required}")
    for forbidden in case.must_not_use_tools:
        if forbidden in tools_used:
            failures.append(f"不应调用工具 {forbidden}")
    if case.reply_contains_any and not any(token in reply for token in case.reply_contains_any):
        failures.append(f"回复未命中任一关键词：{list(case.reply_contains_any)}")
    if case.reply_regex and not re.search(case.reply_regex, reply):
        failures.append(f"回复未命中正则：{case.reply_regex}")
    if turns > case.max_turns:
        failures.append(f"轮数超限：{turns} > {case.max_turns}")
    return failures


def make_dataset_items(cases: list[EvalCase]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case.case_id,
            "prompt": case.prompt,
            "must_use_tools": list(case.must_use_tools),
            "must_not_use_tools": list(case.must_not_use_tools),
            "reply_contains_any": list(case.reply_contains_any),
            "reply_regex": case.reply_regex,
            "max_turns": case.max_turns,
            "model": DEFAULT_MODEL,
        }
        for case in cases
    ]
