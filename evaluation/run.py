"""评测入口：uv run python -m evaluation.run

跑固定任务集（真实 LLM + 真实装配），输出报告并保存 JSON 基线。
"""

from evaluation.report import format_report, save_report
from evaluation.runner import run_eval


def main() -> None:
    results = run_eval()
    report = format_report(results)
    print()
    print(report)
    save_report(results)
    print(f"\n报告已保存: {save_report.__globals__['REPORT_JSON']}")


if __name__ == "__main__":
    main()
