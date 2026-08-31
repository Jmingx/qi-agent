"""评测入口：uv run python -m evaluation.run

跑固定任务集（真实 LLM + 真实装配），输出报告 + 自动对比上次基线（回归告警）。
"""

from evaluation.report import (
    compare,
    format_compare,
    format_report,
    load_report,
    save_report,
)
from evaluation.runner import run_eval
from evaluation.tasks import LONG_TASKS, SUBAGENT_TASKS, TASKS


def main() -> None:
    # 任务集选择（阶段 C 收尾，方案 2026-08-23）：默认快速集 TASKS；
    # --long 跑长对话评测（L3/L4 事实保持，真实 LLM ~5 分钟）；
    # --all 跑全部（TASKS + LONG_TASKS）
    import argparse

    parser = argparse.ArgumentParser(description="qi-agent 评测")
    parser.add_argument("--long", action="store_true",
                        help="跑长对话事实保持评测（L3/L4，真实 LLM ~5 分钟）")
    parser.add_argument("--subagent", action="store_true",
                        help="跑 subagent 委派评测（主 agent 主动用 delegate_task）")
    parser.add_argument("--all", action="store_true", help="跑全部任务")
    args = parser.parse_args()
    tasks = TASKS
    if args.long:
        tasks = LONG_TASKS
    if args.subagent:
        tasks = SUBAGENT_TASKS
    if args.all:
        tasks = TASKS + LONG_TASKS + SUBAGENT_TASKS

    # 提示评测性质：真实 API 调用，需要 DEEPSEEK_API_KEY（.env）
    print(f"开始评测（真实 LLM API，{len(tasks)} 个任务，预计 1-3 分钟）...", flush=True)
    results = run_eval(tasks)
    # 并发执行后按原顺序打印每任务结果
    for r in results:
        mark = "✅" if r["passed"] else "❌"
        detail = "; ".join(r["failures"]) if r["failures"] else ""
        print(f"[评测] {r['id']} {r['name']}: {mark} {detail}（{r['elapsed']}s）", flush=True)
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
