"""严格加载声明式评测套件。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from evaluation.case_models import EvalCase, case_from_payload

SUITES_DIR = Path(__file__).with_name("suites")


class SuiteLoadError(ValueError):
    """用例文件或行内容不符合套件协议。"""


def available_suite_names(directory: Path = SUITES_DIR) -> tuple[str, ...]:
    """从 JSONL 文件名发现套件，不在 Python 中维护套件白名单。"""
    return tuple(sorted(path.stem for path in Path(directory).glob("*.jsonl")))


def load_suite(path: Path, *, expected_name: str | None = None) -> list[EvalCase]:
    """加载一个 JSONL 文件，并在错误中保留文件名和行号。"""
    path = Path(path)
    if path.suffix != ".jsonl":
        raise SuiteLoadError(f"套件文件名必须以 .jsonl 结尾: {path.name}")
    suite_name = expected_name or path.stem
    if not suite_name:
        raise SuiteLoadError(f"套件名不能为空: {path.name}")
    cases: list[EvalCase] = []
    seen: set[str] = set()
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise SuiteLoadError(f"无法打开套件 {path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            location = f"{path.name}:{line_number}"
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SuiteLoadError(f"{location}: JSON 无效: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise SuiteLoadError(f"{location}: 顶层必须是 JSON object")
            case_id = payload.get("id")
            if not isinstance(case_id, str) or not case_id.strip():
                raise SuiteLoadError(f"{location}: id 必须是非空字符串")
            if case_id in seen:
                raise SuiteLoadError(f"{location}: 重复 case id: {case_id}")
            if "prompt" not in payload and not payload.get("steps"):
                raise SuiteLoadError(f"{location}: 必须提供 prompt 或非空 steps")
            declared = payload.get("suite", suite_name)
            if declared != suite_name:
                raise SuiteLoadError(
                    f"{location}: suite={declared!r} 与文件套件 {suite_name!r} 不一致"
                )
            try:
                case = case_from_payload(payload, suite=suite_name)
            except (KeyError, TypeError, ValueError) as exc:
                raise SuiteLoadError(f"{location}: 字段类型无效: {exc}") from exc
            seen.add(case_id)
            cases.append(case)
    return cases


def load_suites(names: Iterable[str], directory: Path = SUITES_DIR) -> list[EvalCase]:
    """按传入顺序加载多个套件，并检查跨套件重复 ID。"""
    available = set(available_suite_names(directory))
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for name in names:
        if name not in available:
            raise SuiteLoadError(f"未知套件名: {name}")
        for case in load_suite(directory / f"{name}.jsonl", expected_name=name):
            if case.case_id in seen:
                raise SuiteLoadError(f"套件集合中重复 case id: {case.case_id}")
            seen.add(case.case_id)
            cases.append(case)
    return cases
