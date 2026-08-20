"""评测入口：uv run python -m evaluation.run

跑固定任务集（真实 LLM + 真实装配），输出报告并保存 JSON 基线。
"""

from evaluation.report import format_report, save_report
from evaluation.runner import run_eval


def main() -> None:
    # 提示评测性质：真实 API 调用，需要 DEEPSEEK_API_KEY（.env）
    print("开始评测（真实 LLM API，16 个任务，预计 1-3 分钟）...", flush=True)
    results = run_eval()
    # 并发执行后按原顺序打印每任务结果
    for r in results:
        mark = "✅" if r["passed"] else "❌"
        detail = "; ".join(r["failures"]) if r["failures"] else ""
        print(f"[评测] {r['id']} {r['name']}: {mark} {detail}（{r['elapsed']}s）", flush=True)
    report = format_report(results)
    print()
    print(report, flush=True)
    save_report(results)
    print(f"\n报告已保存: {save_report.__globals__['REPORT_JSON']}", flush=True)


if __name__ == "__main__":
    main()
