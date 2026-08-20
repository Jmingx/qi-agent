"""测评报告：文本汇总 + JSON 落盘 + 回归基线对比。

方案：docs/plans/2026-08-20-测评系统阶段A方案.md + 回归基线对比方案
- save_report：结果落盘（run_at 时间戳包装，作为下次对比的基线）
- load_report：读上次基线（兼容旧纯 list 格式）
- compare / format_compare：回归/改善/新增判定 + 通过率变化 + 告警
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# 报告落盘路径（机器可读，作为回归对比基线）
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
    """结果 JSON 落盘（run_at 时间戳包装，作为下次对比的基线）。"""
    payload = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }
    REPORT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_report() -> tuple[str | None, list[dict]]:
    """读上次基线。返回 (run_at, results)；无基线/格式异常 → (None, [])。

    兼容旧格式（纯 list，v0.4.14 之前）：视为 results，run_at 为 None。
    """
    if not REPORT_JSON.exists():
        return None, []
    try:
        data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, []
    if isinstance(data, list):  # 旧格式兼容
        return None, data
    return data.get("run_at"), data.get("results", [])


def compare(prev: list[dict], current: list[dict]) -> dict:
    """对比两次评测结果：回归/改善/新增 + 通过率变化。

    Args:
        prev: 上次基线结果列表
        current: 本次结果列表

    Returns:
        {"prev_passed", "cur_passed", "total", "delta_pct",
         "regressions": [id...], "improved": [id...], "added": [id...]}
    """
    prev_map = {r["id"]: r for r in prev}
    cur_map = {r["id"]: r for r in current}
    # 回归：本次失败且上次通过；改善：本次通过且上次失败
    regressions = sorted(
        i for i, r in cur_map.items()
        if not r["passed"] and prev_map.get(i, {}).get("passed")
    )
    improved = sorted(
        i for i, r in cur_map.items()
        if r["passed"] and i in prev_map and not prev_map[i]["passed"]
    )
    added = sorted(i for i in cur_map if i not in prev_map)
    prev_passed = sum(1 for r in prev if r["passed"])
    cur_passed = sum(1 for r in current if r["passed"])
    total = len(current)
    delta_pct = (cur_passed - prev_passed) / total * 100 if total else 0.0
    return {
        "prev_passed": prev_passed,
        "cur_passed": cur_passed,
        "total": total,
        "delta_pct": round(delta_pct, 1),
        "regressions": regressions,
        "improved": improved,
        "added": added,
    }


def format_compare(run_at: str | None, cmp: dict) -> str:
    """生成对比文本（▼/▲ + 明细 + 回归告警）。"""
    total = cmp["total"]
    prev_pct = cmp["prev_passed"] / total * 100 if total else 0.0
    cur_pct = cmp["cur_passed"] / total * 100 if total else 0.0
    delta = cmp["delta_pct"]
    arrow = "▼" if delta < 0 else "▲" if delta > 0 else "＝"
    lines = [f"[对比] vs 上次基线（{run_at or '未知'}）"]
    lines.append(
        f"  通过率: {cmp['prev_passed']}/{total} ({prev_pct:.1f}%) → "
        f"{cmp['cur_passed']}/{total} ({cur_pct:.1f}%) {arrow} {delta:+.1f}%"
    )
    if cmp["regressions"]:
        lines.append(f"  回归 {len(cmp['regressions'])}: {', '.join(cmp['regressions'])}")
    if cmp["improved"]:
        lines.append(f"  改善 {len(cmp['improved'])}: {', '.join(cmp['improved'])}")
    if cmp["added"]:
        lines.append(f"  新增 {len(cmp['added'])}: {', '.join(cmp['added'])}")
    if cmp["regressions"]:
        # 非确定性缓解：单次对比可能误报（LLM 波动），引导重跑确认
        lines.append("⚠️ 检测到回归——LLM 非确定性可能导致误报，建议重跑一次确认")
    else:
        lines.append("✅ 无回归")
    return "\n".join(lines)
