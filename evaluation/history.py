"""测评历史完整保留 + 趋势（方案 2026-08-29 Phase 3）。

设计（用户拍板——历史完整保留，趋势分析前提）：
- eval_runs/ 目录：每 run 一个完整 JSON（永不覆盖，含每任务明细）
- runs_index.jsonl：汇总索引（追加式——每 run 追加一行，永不重写）
- 写盘原子性：run 文件先写（tmp + rename）→ 再追加索引

提供：save_run / load_runs / load_run / trends / window_regression /
      task_trend（任务归因）
"""

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

# 历史目录（相对本文件——evaluation/eval_runs/）
EVAL_RUNS_DIR = Path(__file__).parent / "eval_runs"
INDEX_FILE = EVAL_RUNS_DIR / "runs_index.jsonl"


def _ensure_dir() -> None:
    EVAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _run_summary(run_id: str, run_at: str, results: list[dict]) -> dict:
    """从 results 计算单 run 汇总（索引行）。"""
    total = len(results) or 1
    passed = sum(1 for r in results if r["passed"])
    scored = [r["score"] for r in results if r.get("score") is not None]
    return {
        "run_id": run_id,
        "run_at": run_at,
        "task_count": len(results),
        "pass_rate": round(passed / total, 3),
        "passed": passed,
        "avg_score": round(sum(scored) / len(scored), 1) if scored else None,
        "scored_count": len(scored),
        "total_tokens": sum(
            (r.get("tokens") or {}).get("total_tokens", 0) for r in results),
        "total_cost": round(sum(r.get("cost", 0.0) for r in results), 4),
        "avg_elapsed": round(
            sum(r.get("elapsed", 0.0) for r in results) / total, 1),
    }


def save_run(results: list[dict]) -> str:
    """完整保存一次评测（run 文件 + 索引追加）。

    Returns:
        run_id（时间戳——如 20260829_103000）
    """
    _ensure_dir()
    # run_id：时间戳 + 短 uuid（防同秒多次保存冲突——文件覆盖事故）
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    run_at = datetime.now().isoformat(timespec="seconds")
    payload = {"run_id": run_id, "run_at": run_at, "results": results}
    # ① run 文件：tmp + rename 原子写（防半写）
    run_file = EVAL_RUNS_DIR / f"{run_id}_report.json"
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=EVAL_RUNS_DIR, delete=False,
            suffix=".tmp") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp_path = f.name
    os.replace(tmp_path, run_file)
    # ② 索引：追加一行（永不重写历史）
    with open(INDEX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(_run_summary(run_id, run_at, results),
                           ensure_ascii=False) + "\n")
    return run_id


def load_runs() -> list[dict]:
    """读历史 run 汇总列表（索引——按时间正序）。"""
    if not INDEX_FILE.exists():
        return []
    runs: list[dict] = []
    with open(INDEX_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 损坏行跳过（不阻塞历史读取）
    return runs


def load_run(run_id: str) -> dict | None:
    """读某次 run 明细（完整 results）。"""
    run_file = EVAL_RUNS_DIR / f"{run_id}_report.json"
    if not run_file.exists():
        return None
    try:
        return json.loads(run_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def trends(window: int = 5) -> dict:
    """跨 run 指标序列（最近 window 次——趋势图数据）。

    Returns:
        {"run_ids": [...], "pass_rates": [...], "avg_scores": [...],
         "total_tokens": [...], "total_costs": [...], "avg_elapseds": [...]}
    """
    runs = load_runs()[-window:]
    return {
        "run_ids": [r["run_id"] for r in runs],
        "pass_rates": [r["pass_rate"] for r in runs],
        "avg_scores": [r["avg_score"] for r in runs],
        "total_tokens": [r["total_tokens"] for r in runs],
        "total_costs": [r["total_cost"] for r in runs],
        "avg_elapseds": [r["avg_elapsed"] for r in runs],
    }


def window_regression(window: int = 5) -> list[str]:
    """滑动窗口回归检测：本次 vs 最近 window-1 次均值。

    通过率低于窗口均值（或窗口全过本次不过）→ 告警任务/整体。
    趋势比单点对比可靠（LLM 波动容忍——方案 D5）。

    Returns:
        告警列表（空 = 无回归）
    """
    runs = load_runs()
    if len(runs) < 2:
        return []
    current = runs[-1]
    prev = runs[-window - 1:-1]
    if not prev:
        return []
    prev_avg = sum(r["pass_rate"] for r in prev) / len(prev)
    alarms: list[str] = []
    if current["pass_rate"] < prev_avg - 0.1:  # 通过率下降 ≥10 个百分点
        alarms.append(
            f"整体通过率回归: {current['pass_rate']:.0%} < "
            f"窗口均值 {prev_avg:.0%}（最近 {len(prev)} 次）")
    return alarms


def task_trend(task_id: str) -> list[dict]:
    """任务归因：某任务跨 run 的表现序列（score/passed）。

    Returns:
        [{"run_id", "passed", "score"}, ...]（正序）
    """
    out: list[dict] = []
    for run in load_runs():
        detail = load_run(run["run_id"])
        if not detail:
            continue
        for r in detail["results"]:
            if r["id"] == task_id:
                out.append({
                    "run_id": run["run_id"],
                    "passed": r["passed"],
                    "score": r.get("score"),
                })
                break
    return out
