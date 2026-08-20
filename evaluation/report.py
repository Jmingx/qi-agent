"""测评报告：文本汇总 + JSON 落盘（供后续对比）。

方案：docs/plans/2026-08-20-测评系统阶段A方案.md
"""

import json
from collections import defaultdict
from pathlib import Path

# 报告落盘路径（机器可读，供后续对比）
REPORT_JSON = Path(__file__).parent / "last_report.json"


def format_report(results: list[dict]) -> str:
    """生成人类可读的报告文本。"""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    lines = [
        f"[评测] 通过 {passed}/{total}（{passed / total * 100:.1f}%）",
    ]
    # 按类别细分
    by_category: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)
    cats = " | ".join(
        f"{cat} {sum(1 for r in rs if r['passed'])}/{len(rs)}"
        for cat, rs in by_category.items()
    )
    lines.append(f"  按类别: {cats}")
    # 平均轮次/耗时
    if total:
        avg_turns = sum(r["turns"] for r in results) / total
        avg_elapsed = sum(r["elapsed"] for r in results) / total
        lines.append(f"  平均轮次: {avg_turns:.1f} | 平均耗时: {avg_elapsed:.1f}s")
    # 失败明细
    failed = [r for r in results if not r["passed"]]
    if failed:
        lines.append("失败明细:")
        for r in failed:
            lines.append(f"  {r['id']} {r['name']}: {'; '.join(r['failures'])}")
    return "\n".join(lines)


def save_report(results: list[dict]) -> None:
    """结果 JSON 落盘（机器可读，后续对比基线）。"""
    REPORT_JSON.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
