"""统一 JSONL 套件的结构和迁移快照测试。"""

import json
from pathlib import Path

import pytest

from evaluation.run import _filter_case_id
from evaluation.suite_loader import (
    SuiteLoadError,
    available_suite_names,
    load_suite,
    load_suites,
)


def test_all_canonical_suites_load_with_expected_ids() -> None:
    smoke = load_suites(("smoke",))
    regression = load_suites(("regression",))
    long_context = load_suites(("long_context",))
    subagent = load_suites(("subagent",))

    assert [case.case_id for case in smoke] == [
        "time_tool", "math_direct", "list_dir_tool"
    ]
    assert len(regression) == 26
    assert len(long_context) == 4
    assert len(subagent) == 2
    assert all(case.steps for case in regression + long_context + subagent)
    assert load_suites(("regression", "long_context", "subagent"))


def test_suite_names_are_discovered_from_jsonl_files(tmp_path: Path) -> None:
    (tmp_path / "zeta.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "alpha.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")

    assert available_suite_names(tmp_path) == ("alpha", "zeta")


def test_case_id_filter_selects_one_case() -> None:
    tasks = load_suites(("smoke",))

    selected = _filter_case_id(tasks, "time_tool")

    assert [case.id for case in selected] == ["time_tool"]


def test_case_id_filter_rejects_unknown_case() -> None:
    with pytest.raises(ValueError, match="找不到"):
        _filter_case_id(load_suites(("smoke",)), "missing")


def test_loader_reports_line_number_and_duplicate_id(tmp_path: Path) -> None:
    duplicate = tmp_path / "smoke.jsonl"
    duplicate.write_text(
        json.dumps({"id": "x", "suite": "smoke", "steps": ["a"]})
        + "\n"
        + json.dumps({"id": "x", "suite": "smoke", "steps": ["b"]})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SuiteLoadError, match=r"smoke\.jsonl:2.*重复"):
        load_suite(duplicate, expected_name="smoke")


def test_loader_rejects_invalid_json_with_location(tmp_path: Path) -> None:
    invalid = tmp_path / "smoke.jsonl"
    invalid.write_text("{bad json}\n", encoding="utf-8")

    with pytest.raises(SuiteLoadError, match=r"smoke\.jsonl:1.*JSON"):
        load_suite(invalid, expected_name="smoke")
