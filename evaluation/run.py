"""评测入口：uv run python -m evaluation.run

跑固定任务集（真实 LLM + 真实装配），输出报告 + 自动对比上次基线（回归告警）。
"""

import argparse
import datetime as dt

from evaluation.report import (
    compare,
    format_compare,
    format_report,
    load_report,
    save_report,
)
from evaluation.runner import run_eval
from evaluation.suite_loader import available_suite_names, load_suites


def _filter_case_id(tasks: list, case_id: str) -> list:
    """按 ID 缩小调试范围，并拒绝不存在或不唯一的 ID。"""
    matches = [task for task in tasks if task.id == case_id]
    if len(matches) != 1:
        if not matches:
            raise ValueError(f"找不到 case_id={case_id!r}")
        raise ValueError(f"case_id={case_id!r} 不唯一，匹配到 {len(matches)} 条用例")
    return matches


def main() -> None:
    # suite 名称来自 evaluation/suites/*.jsonl，避免新增数据文件还要改 CLI。
    suite_names = available_suite_names()
    parser = argparse.ArgumentParser(description="qi-agent 评测")
    parser.add_argument(
        "--suite",
        choices=(*suite_names, "all"),
        help="选择 evaluation/suites 下同名 JSONL；all=执行全部套件",
    )
    parser.add_argument(
        "--case-id",
        help="只执行指定 case_id；不传 --suite 时在全部 JSONL 套件中查找",
    )
    args, remaining = parser.parse_known_args()
    if remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")
    if args.suite is None and args.case_id is None:
        parser.print_help()
        return
    if args.suite is None or args.suite == "all":
        selected_suites = suite_names
    else:
        selected_suites = (args.suite,)
    tasks = load_suites(selected_suites)
    if args.case_id is not None:
        try:
            tasks = _filter_case_id(tasks, args.case_id)
        except ValueError as exc:
            parser.error(str(exc))

    # 提示评测性质：真实 API 调用，需要 DEEPSEEK_API_KEY（.env）
    print(f"开始评测（真实 LLM API，{len(tasks)} 个任务，预计 1-3 分钟）...", flush=True)
    results = run_eval(tasks)
    # 并发执行后按原顺序打印每任务结果
    for r in results:
        mark = "✅" if r["passed"] else "❌"
        detail = "; ".join(r["failures"]) if r["failures"] else ""
        trace = r.get("jaeger_trace_id") or "-"
        jaeger = r.get("jaeger_url") or "-"
        print(
            f"[评测] {r['id']} {r['name']}: {mark} {detail}"
            f"（{r['elapsed']}s） trace_id={trace} jaeger_url={jaeger}",
            flush=True,
        )
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        from evaluation.opik_reporter import upload_results

        opik_summary = upload_results(
            results, run_id, project="qi-agent-evaluation"
        )
        print(
            f"[opik] project=qi-agent-evaluation "
            f"datasets={opik_summary['experiments']} "
            f"dataset_items={opik_summary['dataset_items']} "
            f"experiments={opik_summary['experiments']} "
            f"traces={opik_summary['traces']}",
            flush=True,
        )
        for suite in opik_summary["suites"]:
            print(
                f"  [opik] suite={suite['suite']} dataset={suite['dataset']} "
                f"experiment={suite['experiment']}",
                flush=True,
            )
        for result in results:
            if result.get("opik_trace_id"):
                print(
                    f"  [case:{result['id']}] opik_trace_id={result['opik_trace_id']}",
                    flush=True,
                )
    except Exception as exc:
        print(f"[opik] 上传失败（不影响规则评测结果）: {exc}", flush=True)
    report = format_report(results)
    print()
    print(report, flush=True)
    # 回归基线对比：读上次 → 对比 → 打印（无上次则跳过）
    prev_run_at, prev = load_report()
    if prev:
        print()
        print(format_compare(prev_run_at, compare(prev, results)), flush=True)
    save_report(results)  # 覆盖为本次（即新基线）
    from evaluation.history import save_run

    run_id = save_run(results)  # 历史完整保留（方案 2026-08-29 Phase 3）
    print(f"\n报告已保存: {save_report.__globals__['REPORT_JSON']}", flush=True)
    print(f"历史已归档: eval_runs/{run_id}_report.json", flush=True)


if __name__ == "__main__":
    main()
