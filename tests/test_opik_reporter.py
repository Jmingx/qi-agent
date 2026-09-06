"""Opik Dataset/Experiment/Trace 关联测试。"""

from __future__ import annotations

from types import SimpleNamespace

import opik

from evaluation.opik_reporter import upload_results


class FakeDataset:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    def insert(self, items: list[dict], deduplication: bool = True) -> None:
        self.inserted.extend(items)

    def get_items(self) -> list[dict]:
        return [
            {"id": f"dataset-{item['input']['case_id']}", **item}
            for item in self.inserted
        ]


class FakeExperiment:
    def __init__(self, name: str) -> None:
        self.name = name
        self.references = []

    def insert(self, references: list) -> None:
        self.references.extend(references)


class FakeOpikClient:
    def __init__(self) -> None:
        self.dataset = FakeDataset()
        self.experiment: FakeExperiment | None = None
        self.traces: list[dict] = []
        self.flush_count = 0

    def trace(self, **kwargs):
        self.traces.append(kwargs)
        return SimpleNamespace(id=f"trace-{kwargs['input']['case_id']}")

    def get_or_create_dataset(self, name: str, **kwargs):
        assert name == "qi-agent-regression"
        return self.dataset

    def create_experiment(self, dataset_name: str, name: str, **kwargs):
        assert dataset_name == "qi-agent-regression"
        self.experiment = FakeExperiment(name)
        return self.experiment

    def flush(self) -> None:
        self.flush_count += 1


def test_upload_results_creates_dataset_experiment_and_trace_links(monkeypatch) -> None:
    client = FakeOpikClient()
    monkeypatch.setattr(opik, "Opik", lambda **kwargs: client)
    results = [
        {
            "id": "t1",
            "suite": "regression",
            "name": "问时间",
            "prompt": "现在几点？",
            "steps": ["现在几点？"],
            "expected_tools": ["get_time"],
            "expected_keywords": [],
            "expected_rubric": None,
            "category": "tool",
            "passed": True,
            "reply": "现在是 12 点",
            "tools_used": ["get_time"],
            "failures": [],
            "elapsed": 1.2,
            "session_id": "session-t1",
            "jaeger_trace_id": "jaeger-t1",
        }
    ]

    summary = upload_results(results, "20260906-200000")

    assert summary["dataset_items"] == 1
    assert summary["experiments"] == 1
    assert summary["traces"] == 1
    assert client.traces[0]["metadata"]["suite"] == "regression"
    output = client.traces[0]["output"]
    assert output["case_id"] == "t1"
    assert output["session_id"] == "session-t1"
    assert output["elapsed_s"] == 1.2
    assert output["latency_ms"] == 1200
    assert output["jaeger_trace_id"] == "jaeger-t1"
    assert output["model"] == "deepseek-v4-flash"
    assert client.experiment is not None
    assert len(client.experiment.references) == 1
    assert client.experiment.references[0].dataset_item_id == "dataset-t1"
    assert client.experiment.references[0].trace_id == "trace-t1"
    assert results[0]["opik_trace_id"] == "trace-t1"
