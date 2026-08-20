"""评测入口：uv run python -m evaluation.run

跑固定任务集（真实 LLM + 真实装配），输出报告并保存 JSON 基线。
"""

from evaluation.report import format_report, save_report
from evaluation.runner import run_eval


def main() -> None:
    results = run_eval()
    # 并发执行后按原顺序打印每任务结果
    for r in results:
        mark = "✅" if r["passed"] else "❌"
        detail = "; ".join(r["failures"]) if r["failures"] else ""
        print(f"[评测] {r['id']} {r['name']}: {mark} {detail}（{r['elapsed']}s）")
    report = format_report(results)
    print()
    print(report)
    save_report(results)
    print(f"\n报告已保存: {save_report.__globals__['REPORT_JSON']}")


if __name__ == "__main__":
    main()
