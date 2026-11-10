"""通过评测 Gateway 黑盒执行正式 EvalTask。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from evaluation.gateway_client import GatewayRpcClient
from evaluation.case_models import EvalCase
from evaluation.runner import estimate_cost, judge, score_task


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8772
DEFAULT_JAEGER_URL = os.getenv(
    "QI_JAEGER_URL", "http://127.0.0.1:16686/jaeger"
).rstrip("/")


def _socket_ready(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _start_server(host: str, port: int) -> subprocess.Popen[str] | None:
    if _socket_ready(host, port):
        return None
    executable = str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(executable).exists():
        import sys

        executable = sys.executable
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        [executable, "-m", "qi_agent.evaluation_serve", "--host", host,
         "--port", str(port)],
        cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), text=True,
        env=child_env,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if _socket_ready(host, port):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"评测 Gateway 启动失败: {proc.returncode}")
        time.sleep(0.5)
    proc.terminate()
    raise TimeoutError(f"等待评测 Gateway 超时: {host}:{port}")


def _stop_server(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _jaeger_url(trace_id: str, base_url: str = DEFAULT_JAEGER_URL) -> str:
    return f"{base_url.rstrip('/')}/trace/{trace_id}" if trace_id else "-"


def _preconditions(task: EvalCase) -> dict[str, Any]:
    """只消费 JSONL 声明的旁路 fixture，不再按 case id 隐式补逻辑。"""
    value = dict(getattr(task, "preconditions", None) or {})
    context = dict(value.get("context") or {})
    if task.plugin_overrides:
        context.setdefault("plugin_overrides", task.plugin_overrides)
    # 仅兼容外部仍手工构造旧 EvalTask 的测试/脚本；JSONL EvalCase 不走此分支。
    if not hasattr(task, "case_id"):
        if task.id == "c-long-3" and "workspace" not in context:
            context["workspace"] = {
                "todos": [{"title": "写周报", "status": "pending"}]
            }
    value["context"] = context
    return value


async def _run_one(task: EvalCase, uri: str, run_id: str) -> dict[str, Any]:
    start = time.perf_counter()
    session_id = ""
    status: dict[str, Any] = {}
    inspected: dict[str, Any] = {}
    trace_id = ""
    error: str | None = None
    async with GatewayRpcClient(uri) as client:
        prepared = await client.call("eval/prepare", {
            "case_id": task.id,
            "run_id": run_id,
            "goal": task.name,
            "preconditions": _preconditions(task),
        })
        session_id = str(prepared["session_id"])
        try:
            for step in task.conversation_steps():
                send_result = await asyncio.wait_for(
                    client.call("message/send", {
                        "session_id": session_id, "text": step,
                    }),
                    timeout=task.timeout,
                )
                reply = str(send_result.get("reply") or client.collected_delta())
            status = await client.call("session/status", {"session_id": session_id})
            inspected = await client.call("eval/inspect", {"session_id": session_id})
            trace = await client.call("session/trace", {"session_id": session_id})
            trace_id = str(trace.get("trace_id") or "")
        except Exception as exc:
            error = str(exc)
        finally:
            await client.call("eval/cleanup", {
                "session_id": session_id,
                "fixture_scope_id": prepared.get("fixture_scope_id", ""),
            })
        tools = list(dict.fromkeys(client.tool_calls))
        tool_details = list(client.tool_call_details)
        reply = locals().get("reply", client.collected_delta())
    history = inspected.get("messages") or []
    if error:
        failures = [f"执行异常: {error}"]
        passed = False
    else:
        passed, failures = judge(task, history)
    if task.expected_memory and not error:
        target, keyword = task.memory_target, task.expected_memory
        if keyword.startswith("user:"):
            target, keyword = "user", keyword[5:]
        elif keyword.startswith("memory:"):
            target, keyword = "memory", keyword[7:]
        entries = (inspected.get("memory") or {}).get(target, [])
        if not any(keyword in entry for entry in entries):
            passed = False
            failures.append(f"期望记忆未写入 {target.upper()}.md: {keyword}")
    score = score_task(task, history, failures)
    usage = inspected.get("usage") or {}
    elapsed = round(time.perf_counter() - start, 1)
    return {
        "id": task.id, "name": task.name, "category": task.category,
        "suite": task.suite, "prompt": task.prompt,
        "steps": list(task.conversation_steps()),
        "expected_tools": list(task.expected_tools),
        "expected_keywords": list(task.expected_keywords),
        "expected_rubric": task.expected_rubric,
        "passed": passed, "failures": failures, "score": score,
        "turns": int(inspected.get("turn") or status.get("turn") or 0),
        "elapsed": elapsed, "tokens": dict(usage),
        "cost": estimate_cost(usage), "session_id": session_id,
        "jaeger_trace_id": trace_id,
        "jaeger_url": _jaeger_url(trace_id),
        "tools_used": tools, "tool_call_details": tool_details,
        "reply": reply,
    }


async def _run_all(tasks: list[EvalCase], host: str, port: int) -> list[dict[str, Any]]:
    proc = _start_server(host, port)
    try:
        uri = f"ws://{host}:{port}"
        run_id = time.strftime("%Y%m%d-%H%M%S")
        results = []
        for task in tasks:
            results.append(await _run_one(task, uri, run_id))
        return results
    finally:
        _stop_server(proc)


def run_gateway_eval(
    tasks: list[EvalCase] | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> list[dict[str, Any]]:
    """串行运行统一 Gateway 评测，串行是 fixture/全局资源隔离的边界。"""
    return asyncio.run(_run_all(tasks or [], host, port))
