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

from case_models import (
    CaseResult,
    EvalCase,
    estimate_cost,
    load_cases,
    make_dataset_items,
    validate_case,
)
from ws_rpc import WsRpcClient, start_serve_if_needed


DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8771
DEFAULT_OPIK_URL = "http://127.0.0.1:5173/api"
DEFAULT_WORKSPACE = "default"
DEFAULT_PROJECT = "qi-agent-smoke"
DEFAULT_DATASET = "qi-agent-smoke"
DEFAULT_EXPERIMENT = f"{dt.date.today().isoformat()}-qi-agent-smoke"
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
        return client.get_experiment_by_name(experiment_name, project_name=project_name)
    except Exception:
        return client.create_experiment(
            dataset_name=dataset_name,
            name=experiment_name,
            project_name=project_name,
        )


async def _run_one_case(serve_url: str, case: EvalCase) -> CaseResult:
    start = time.perf_counter()
    async with WsRpcClient(serve_url) as ws_client:
        create_result = await ws_client.call("session/create", {"goal": case.prompt})
        session_id = str(create_result["session_id"])
        send_result = await ws_client.call(
            "message/send",
            {"session_id": session_id, "text": case.prompt},
        )
        reply = str(send_result.get("reply") or ws_client.collected_delta())
        status = await ws_client.call("session/status", {"session_id": session_id})
        usage = await ws_client.call("context/usage", {"session_id": session_id})
        tools_used = list(dict.fromkeys(ws_client.tool_calls))
    turns = int(status.get("turn") or 0)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
    failures = validate_case(case, reply, turns, tools_used)
    return CaseResult(
        case_id=case.case_id,
        session_id=session_id,
        reply=reply,
        turns=turns,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        elapsed_s=time.perf_counter() - start,
        estimated_cost_rmb=estimate_cost(prompt_tokens, completion_tokens),
        tools_used=tools_used,
        passed=not failures,
        failures=failures,
    )


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
    experiment_name: str,
    project_name: str,
) -> tuple[int, int]:
    dataset = client.get_dataset(dataset_name, project_name=project_name)
    experiment = client.get_experiment_by_name(experiment_name, project_name=project_name)
    dataset_count = len(list(dataset.get_items()))
    experiment_count = len(list(experiment.get_items()))
    return dataset_count, experiment_count


async def _run(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    serve_proc = None
    if not args.no_start_serve:
        serve_proc = start_serve_if_needed(args.serve_host, args.serve_port)
    client = _ensure_opik_client(args.opik_url, args.workspace, args.project_name)
    results: list[CaseResult] = []
    for case in cases:
        result = await _run_one_case(args.serve_url, case)
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
    experiment, _ = _upload_results(
        client=client,
        dataset_name=args.dataset_name,
        experiment_name=args.experiment_name,
        project_name=args.project_name,
        cases=cases,
        results=results,
    )
    dataset_count, experiment_count = await asyncio.to_thread(
        _verify_opik_state,
        client,
        args.dataset_name,
        args.experiment_name,
        args.project_name,
    )
    print(
        f"[opik] dataset={args.dataset_name} items={dataset_count} "
        f"experiment={args.experiment_name} items={experiment_count} "
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
    parser.add_argument("--experiment-name", default=DEFAULT_EXPERIMENT)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run(args))
