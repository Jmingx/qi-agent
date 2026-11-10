"""Smoke 评测归并后的 canonical 目录测试。"""

from pathlib import Path


def test_smoke_suite_uses_canonical_path() -> None:
    from evaluation.case_models import load_cases

    expected = Path(__file__).parents[1] / "evaluation" / "suites" / "smoke.jsonl"
    assert len(load_cases(expected)) == 3
