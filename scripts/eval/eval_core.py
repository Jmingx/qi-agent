"""Opik 评测接入 POC 的核心逻辑。"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import subprocess
import time
from pathlib import Path
from typing import Any

import opik

try:
    from case_models import (
        CaseResult,
        EvalCase,
        estimate_cost,
        load_cases,
        make_dataset_items,
        validate_case,
    )
    from ws_rpc import WsRpcClient, start_serve_if_needed
except ModuleNotFoundError:  # 允许测试以 scripts.eval.eval_core 方式导入
    from scripts.eval.case_models import (
        CaseResult,
        EvalCase,
        estimate_cost,
        load_cases,
        make_dataset_items,
        validate_case,
    )
    from scripts.eval.ws_rpc import WsRpcClient, start_serve_if_needed


DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8771
DEFAULT_OPIK_URL = "http://127.0.0.1:5173/api"
DEFAULT_WORKSPACE = "default"
DEFAULT_PROJECT = "qi-agent-smoke"
DEFAULT_DATASET = "qi-agent-smoke"
CASES_PATH = Path(__file__).with_name("cases.jsonl")


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _ensure_opik_client(host: str, workspace: str, project_name: str) -> opik.Opik:
    return opik.Opik(host=host, workspace=workspace, project_name=project_name)


def _create_or_get_experiment(
    client: opik.Opik,
    dataset_name: str,
    experiment_name: str,
    project_name: str,
) -> Any:
    try:
        experiments = client.get_experiments_by_name(
            experiment_name,
            project_name=project_name,
        )
    except opik.exceptions.ExperimentNotFound:
        # Opik SDK 首次查询不存在的名称时抛异常，而不是返回空列表。
        # 将“未找到”转换为空结果，才能继续走创建分支。
        experiments = []
    if experiments:
        return experiments[0]
    return client.create_experiment(
        dataset_name=dataset_name,
        name=experiment_name,
        project_name=project_name,
    )


async def _run_one_case(
    serve_url: str,
    case: EvalCase,
    run_id: str,
) -> CaseResult:
    start = time.perf_counter()
    async with WsRpcClient(serve_url) as ws_client:
        create_result = await ws_client.call(
            "session/create",
            {
                "goal": case.prompt,
                "metadata": {
                    "eval_case_id": case.case_id,
                    "eval_run_id": run_id,
                },
            },
        )
        session_id = str(create_result["session_id"])
        send_result = await ws_client.call(
            "message/send",
            {"session_id": session_id, "text": case.prompt},
        )
        reply = str(send_result.get("reply") or ws_client.collected_delta())
        status = await ws_client.call("session/status", {"session_id": session_id})
        usage = await ws_client.call("context/usage", {"session_id": session_id})
        trace_result = await ws_client.call("session/trace", {"session_id": session_id})
        tools_used = list(dict.fromkeys(ws_client.tool_calls))
        jaeger_trace_id = str(trace_result.get("trace_id") or "")
    elapsed_s = time.perf_counter() - start
    turns = int(status.get("turn") or 0)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
    failures = validate_case(
        case,
        reply,
        turns,
        tools_used,
        tool_calls=list(ws_client.tool_call_details),
    )
    return CaseResult(
        case_id=case.case_id,
        session_id=session_id,
        reply=reply,
        turns=turns,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        elapsed_s=elapsed_s,
        estimated_cost_rmb=estimate_cost(prompt_tokens, completion_tokens),
        tools_used=tools_used,
        passed=not failures,
        failures=failures,
        latency_ms=round(elapsed_s * 1000),
        jaeger_trace_id=jaeger_trace_id,
    )


def _upload_opik_trace(
    client: opik.Opik,
    case: EvalCase,
    result: CaseResult,
    started_at: dt.datetime,
    ended_at: dt.datetime,
    run_id: str,
    project_name: str,
) -> None:
    """写入一条结果级 Opik Trace，展示评测端到端 latency。"""
    trace = client.trace(
        name=f"eval/{case.case_id}",
        start_time=started_at,
        end_time=ended_at,
        input={"case_id": case.case_id, "prompt": case.prompt[:200]},
        output={
            "passed": result.passed,
            "reply": result.reply[:200],
            "tools_used": result.tools_used,
        },
        metadata={
            "case_id": case.case_id,
            "session_id": result.session_id,
            "jaeger_trace_id": result.jaeger_trace_id,
            "run_id": run_id,
        },
        tags=["evaluation", f"case:{case.case_id}"],
        thread_id=result.session_id,
        project_name=project_name,
    )
    result.opik_trace_id = str(getattr(trace, "id", "") or "")


def _upload_results(
    client: opik.Opik,
    dataset_name: str,
    experiment_name: str,
    project_name: str,
    cases: list[EvalCase],
    results: list[CaseResult],
) -> tuple[Any, list[dict[str, Any]]]:
    dataset = client.get_or_create_dataset(dataset_name, project_name=project_name)
    dataset.insert(make_dataset_items(cases))
    dataset_items = list(dataset.get_items())
    item_by_case_id = {_item_value(item, "case_id"): item for item in dataset_items}
    experiment = _create_or_get_experiment(client, dataset_name, experiment_name, project_name)
    bulk_items: list[dict[str, Any]] = []
    for case, result in zip(cases, results, strict=True):
        dataset_item = item_by_case_id.get(case.case_id)
        if dataset_item is None:
            raise RuntimeError(f"找不到 dataset item: {case.case_id}")
        bulk_items.append(
            {
                "dataset_item_id": str(_item_value(dataset_item, "id")),
                "evaluate_task_result": result.to_experiment_payload(),
                "feedback_scores": result.to_feedback_scores(case),
            }
        )
    client.rest_client.experiments.experiment_items_bulk(
        experiment_name=experiment_name,
        dataset_name=dataset_name,
        items=bulk_items,
        experiment_id=str(_item_value(experiment, "id")),
        project_name=project_name,
    )
    return experiment, dataset_items


def _verify_opik_state(
    client: opik.Opik,
    dataset_name: str,
    experiment_id: str,
    project_name: str,
) -> tuple[int, int]:
    dataset = client.get_dataset(dataset_name, project_name=project_name)
    experiment = client.get_experiment_by_id(experiment_id)
    if experiment is None:
        raise RuntimeError(f"找不到 experiment: {experiment_id}")
    dataset_count = len(list(dataset.get_items()))
    experiment_count = len(list(experiment.get_items()))
    return dataset_count, experiment_count


async def _run(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    run_id = dt.datetime.now().strftime("%H%M%S%f")[:-3]
    experiment_name = args.experiment_name or (
        f"{dt.date.today().isoformat()}-qi-agent-smoke-{run_id}"
    )
    serve_proc = None
    if not args.no_start_serve:
        serve_proc = start_serve_if_needed(args.serve_host, args.serve_port)
    client = _ensure_opik_client(args.opik_url, args.workspace, args.project_name)
    results: list[CaseResult] = []
    timings: list[tuple[dt.datetime, dt.datetime]] = []
    for case in cases:
        started_at = dt.datetime.now(dt.timezone.utc)
        result = await _run_one_case(args.serve_url, case, run_id)
        timings.append((started_at, dt.datetime.now(dt.timezone.utc)))
        results.append(result)
        print(
            f"[case:{case.case_id}] session_id={result.session_id} "
            f"turns={result.turns} tokens={result.total_tokens} "
            f"elapsed={result.elapsed_s:.2f}s cost≈¥{result.estimated_cost_rmb:.6f} "
            f"tools={result.tools_used} passed={result.passed}"
        )
        if result.failures:
            for failure in result.failures:
                print(f"  - {failure}")
        print(f"  reply: {result.reply}")
    for case, result, (started_at, ended_at) in zip(
        cases, results, timings, strict=True
    ):
        _upload_opik_trace(
            client,
            case,
            result,
            started_at,
            ended_at,
            run_id,
            args.project_name,
        )
    client.flush()
    experiment, _ = _upload_results(
        client=client,
        dataset_name=args.dataset_name,
        experiment_name=experiment_name,
        project_name=args.project_name,
        cases=cases,
        results=results,
    )
    dataset_count, experiment_count = await asyncio.to_thread(
        _verify_opik_state,
        client,
        args.dataset_name,
        str(_item_value(experiment, "id")),
        args.project_name,
    )
    print(
        f"[opik] dataset={args.dataset_name} items={dataset_count} "
        f"experiment={experiment_name} items={experiment_count} "
        f"project={args.project_name}"
    )
    print(f"[opik] experiment_id={_item_value(experiment, 'id')} host={args.opik_url}")
    if serve_proc is not None:
        serve_proc.terminate()
        try:
            serve_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            serve_proc.kill()
    return 0 if all(result.passed for result in results) else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="qi-agent Opik smoke runner")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--serve-url", default=f"ws://{DEFAULT_SERVE_HOST}:{DEFAULT_SERVE_PORT}")
    parser.add_argument("--serve-host", default=DEFAULT_SERVE_HOST)
    parser.add_argument("--serve-port", type=int, default=DEFAULT_SERVE_PORT)
    parser.add_argument("--no-start-serve", action="store_true")
    parser.add_argument("--opik-url", default=DEFAULT_OPIK_URL)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--project-name", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--experiment-name", default=None)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run(args))
