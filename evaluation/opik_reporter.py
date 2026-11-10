"""把统一评测结果写入 Opik Dataset、Experiment 和 Trace。"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any


PROJECT_NAME = "qi-agent-evaluation"


def _dataset_name(suite: str) -> str:
    return f"qi-agent-{suite or 'unknown'}"


def _dataset_item(result: dict[str, Any]) -> dict[str, Any]:
    """把执行结果中的用例信息转换为稳定、可去重的 Dataset Item。"""
    return {
        "input": {
            "case_id": result["id"],
            "name": result.get("name", ""),
            "prompt": result.get("prompt", ""),
            "steps": result.get("steps", []),
        },
        "expected_output": {
            "expected_tools": result.get("expected_tools", []),
            "expected_keywords": result.get("expected_keywords", []),
            "rubric": result.get("expected_rubric"),
        },
        "metadata": {
            "case_id": result["id"],
            "suite": result.get("suite", ""),
            "category": result.get("category", ""),
        },
    }


def _dataset_item_ids(dataset: Any) -> dict[str, str]:
    """读取 Dataset Item ID，供 Experiment Item 建立关联。"""
    result: dict[str, str] = {}
    for item in dataset.get_items():
        data = item.get("input") or item.get("data") or {}
        case_id = str(data.get("case_id") or item.get("case_id") or "")
        item_id = str(item.get("id") or "")
        if case_id and item_id:
            result[case_id] = item_id
    return result


def _result_output(result: dict[str, Any]) -> dict[str, Any]:
    """保留旧 Experiment result 的完整字段，避免 Opik 页面只剩摘要字段。"""
    usage = result.get("tokens") or {}
    elapsed = float(result.get("elapsed") or 0.0)
    return {
        "case_id": result["id"],
        "session_id": result.get("session_id", ""),
        "reply": str(result.get("reply") or "")[:500],
        "turns": int(result.get("turns") or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "elapsed_s": round(elapsed, 3),
        "latency_ms": int(result.get("latency_ms") or round(elapsed * 1000)),
        "estimated_cost_rmb": round(float(result.get("cost") or 0.0), 6),
        "tools_used": result.get("tools_used") or [],
        "jaeger_trace_id": result.get("jaeger_trace_id", ""),
        "opik_trace_id": result.get("opik_trace_id", ""),
        "passed": bool(result.get("passed")),
        "failures": result.get("failures") or [],
        "model": result.get("model", "deepseek-v4-flash"),
    }


def _create_trace(
    client: Any,
    result: dict[str, Any],
    run_id: str,
    project: str,
    trace_id: str,
) -> Any:
    elapsed = float(result.get("elapsed") or 0.0)
    now = dt.datetime.now(dt.timezone.utc)
    return client.trace(
        id=trace_id,
        name=f"eval/{result['id']}",
        start_time=now - dt.timedelta(seconds=elapsed),
        end_time=now,
        input={
            "case_id": result["id"],
            "prompt": result.get("prompt", ""),
            "steps": result.get("steps", []),
        },
        output=_result_output(result),
        metadata={
            "case_id": result["id"],
            "suite": result.get("suite", ""),
            "run_id": run_id,
            "session_id": result.get("session_id", ""),
            "jaeger_trace_id": result.get("jaeger_trace_id", ""),
        },
        tags=[
            "evaluation",
            f"suite:{result.get('suite') or 'unknown'}",
            f"case:{result['id']}",
            f"run:{run_id}",
        ],
        thread_id=result.get("session_id") or None,
        project_name=project,
    )


def _experiment_item_reference(
    *, dataset_item_id: str, trace_id: str, project: str
) -> Any:
    from opik.api_objects.experiment.experiment_item import ExperimentItemReferences

    return ExperimentItemReferences(
        dataset_item_id=dataset_item_id,
        trace_id=trace_id,
        project_name=project,
    )


def _upload_suite(
    client: Any,
    results: list[dict[str, Any]],
    run_id: str,
    project: str,
    suite: str,
) -> dict[str, Any]:
    dataset_name = _dataset_name(suite)
    dataset = client.get_or_create_dataset(
        dataset_name,
        description=f"qi-agent evaluation suite: {suite}",
        project_name=project,
    )
    dataset.insert([_dataset_item(result) for result in results], deduplication=True)
    client.flush()
    item_ids = _dataset_item_ids(dataset)
    experiment = client.create_experiment(
        dataset_name=dataset_name,
        name=f"{run_id}-{suite}",
        project_name=project,
        tags=["evaluation", f"suite:{suite}", f"run:{run_id}"],
    )
    references = []
    for result in results:
        item_id = item_ids.get(result["id"])
        trace_id = str(result.get("opik_trace_id") or "")
        if not item_id or not trace_id:
            raise RuntimeError(
                f"无法关联 Opik Experiment Item: case_id={result['id']}"
            )
        references.append(
            _experiment_item_reference(
                dataset_item_id=item_id,
                trace_id=trace_id,
                project=project,
            )
        )
    experiment.insert(references)
    client.flush()
    return {
        "suite": suite,
        "dataset": dataset_name,
        "experiment": experiment.name,
        "dataset_items": len(results),
        "traces": len(results),
    }


def upload_results(
    results: list[dict[str, Any]],
    run_id: str,
    project: str = PROJECT_NAME,
) -> dict[str, Any]:
    """按 suite 创建 Dataset/Experiment，并将每个 case 关联到对应 Trace。"""
    try:
        import opik
    except ImportError:
        return {"suites": [], "dataset_items": 0, "experiments": 0, "traces": 0}

    client = opik.Opik(
        host=os.getenv("QI_OPIK_URL", "http://127.0.0.1:5173/api"),
        workspace=os.getenv("QI_OPIK_WORKSPACE", "default"),
        project_name=project,
    )
    from opik import id_helpers

    for result in results:
        trace_id = id_helpers.generate_id()
        result["opik_trace_id"] = trace_id
        trace = _create_trace(client, result, run_id, project, trace_id)
        result["opik_trace_id"] = str(getattr(trace, "id", "") or "")
    client.flush()

    summaries = []
    for suite in sorted({str(result.get("suite") or "unknown") for result in results}):
        suite_results = [
            result for result in results if str(result.get("suite") or "unknown") == suite
        ]
        summaries.append(_upload_suite(client, suite_results, run_id, project, suite))
    return {
        "suites": summaries,
        "dataset_items": sum(item["dataset_items"] for item in summaries),
        "experiments": len(summaries),
        "traces": sum(item["traces"] for item in summaries),
    }
