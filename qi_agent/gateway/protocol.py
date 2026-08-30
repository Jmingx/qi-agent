"""JSON-RPC 2.0 协议核心（方案 2026-08-28-内核外壳分离——自实现）。

对齐 Codex App Server / DSH sdk：业界主流 agent 自实现协议层
（通用库做不了审批双向流/流式事件定制）。

消息模型（JSON-RPC 2.0）：
  请求（带 id）：{"jsonrpc":"2.0","id":1,"method":"X","params":{...}}
  响应（对应 id）：{"jsonrpc":"2.0","id":1,"result":{...}}
               或 {"jsonrpc":"2.0","id":1,"error":{"code":N,...}}
  通知（无 id）：{"jsonrpc":"2.0","method":"Y","params":{...}}

错误码（JSON-RPC 标准 + 自定义）：
  -32700 解析错误 / -32600 无效请求 / -32601 方法不存在
  -32602 无效参数 / -32603 内部错误
  -32001 会话不存在 / -32002 并发运行（RUNNING 拒绝）
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

# ── 错误码 ─────────────────────────────────────────────────────────────
ERROR_PARSE = -32700
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL = -32603
ERROR_SESSION_NOT_FOUND = -32001
ERROR_CONCURRENT_RUN = -32002

# ── RPC 日志（写本地文件——~/.qi-agent/logs/rpc.log）───────────────────
_RPC_LOG_DIR = os.path.join(os.path.expanduser("~"), ".qi-agent", "logs")


def _get_rpc_logger() -> logging.Logger:
    """获取 RPC 日志器（文件 handler——写 ~/.qi-agent/logs/rpc.log）。

    默认只写文件（不污染 CLI 输出）；debug 模式可加 StreamHandler。
    """
    logger = logging.getLogger("qi_agent.rpc")
    if not logger.handlers:  # 幂等（只配一次）
        os.makedirs(_RPC_LOG_DIR, exist_ok=True)
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(
            os.path.join(_RPC_LOG_DIR, "rpc.log"),
            encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    return logger

# ── 消息模型 ────────────────────────────────────────────────────────────


@dataclass
class RpcRequest:
    """请求（带 id——需要响应）。"""

    id: int | str
    method: str
    params: dict = field(default_factory=dict)


@dataclass
class RpcNotification:
    """通知（无 id——不需要响应）。"""

    method: str
    params: dict = field(default_factory=dict)

    def to_json(self) -> str:
        msg: dict = {"jsonrpc": "2.0", "method": self.method,
                     "params": self.params}
        return json.dumps(msg, ensure_ascii=False)


@dataclass
class RpcResponse:
    """响应（对应请求 id）。result 与 error 二选一。"""

    id: int | str | None
    result: Any = None
    error: dict | None = None

    def to_json(self) -> str:
        msg: dict = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            msg["error"] = self.error
        else:
            msg["result"] = self.result
        return json.dumps(msg, ensure_ascii=False)


def parse_message(raw: str) -> RpcRequest | RpcNotification:
    """解析一条 JSON-RPC 消息（请求或通知）。

    Raises:
        ValueError: 非法 JSON / 无效请求（含错误码信息）
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ERROR_PARSE} 解析错误: {exc}") from exc
    if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
        raise ValueError(f"{ERROR_INVALID_REQUEST} 无效请求")
    method = data.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError(f"{ERROR_INVALID_REQUEST} 缺少 method")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"{ERROR_INVALID_PARAMS} params 必须是对象")
    if "id" in data:
        return RpcRequest(id=data["id"], method=method, params=params)
    return RpcNotification(method=method, params=params)


class RpcError(Exception):
    """带错误码的 RPC 异常（handler 抛出 → dispatch 保留错误码）。

    用法：raise RpcError(ERROR_SESSION_NOT_FOUND, "会话不存在")
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message


def log_rpc(method: str):
    """RPC 方法日志装饰器（可观测——写本地文件 ~/.qi-agent/logs/rpc.log）。

    日志：[RPC] <method> args=<参数摘要> → <结果摘要> (<耗时>ms)
    异常也记录（不吞）：[RPC] <method> ERROR <错误信息>
    写文件不 print——不污染 CLI 交互输出（可审计/排查）。

    用法：
        @log_rpc("session/create")
        def _create_session(self, goal="") -> dict: ...
    """

    def decorator(fn: Callable) -> Callable:
        import functools
        import time

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Any:
            logger = _get_rpc_logger()
            start = time.perf_counter()
            # 参数摘要（截断——防敏感/超长）
            arg_summary = _summarize(kwargs or {})
            try:
                result = fn(*args, **kwargs)
                ms = int((time.perf_counter() - start) * 1000)
                logger.info(f"[RPC] {method} args={arg_summary} "
                            f"→ {_summarize(result)} ({ms}ms)")
                return result
            except Exception as exc:
                ms = int((time.perf_counter() - start) * 1000)
                logger.info(f"[RPC] {method} args={arg_summary} "
                            f"ERROR {exc} ({ms}ms)")
                raise

        return wrapper

    return decorator


def _summarize(obj: Any, limit: int = 120) -> str:
    """对象摘要（截断 + JSON 化——日志可读）。"""
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    return text[:limit] + ("..." if len(text) > limit else "")


# ── dispatch 核心 ───────────────────────────────────────────────────────


class RpcDispatcher:
    """方法注册 + 分发（JSON-RPC 服务端核心）。

    用法：
        d = RpcDispatcher()
        d.register("message/send", handler)
        response_json = d.dispatch('{"method":"message/send",...}')
    """

    def __init__(self) -> None:
        self._methods: dict[str, Callable] = {}

    def register(self, method: str, handler: Callable) -> None:
        """注册方法（method 名 → 处理函数）。"""
        self._methods[method] = handler

    def dispatch(self, raw: str) -> str:
        """处理一条消息，返回响应 JSON（通知返回空串——无需响应）。"""
        try:
            msg = parse_message(raw)
        except ValueError as exc:
            # 解析错误：id 未知（无法对应请求）——标准返回 null id
            return RpcResponse(id=None, error={
                "code": ERROR_PARSE, "message": str(exc)}).to_json()

        if isinstance(msg, RpcNotification):
            # 通知：执行但无响应
            handler = self._methods.get(msg.method)
            if handler is not None:
                try:
                    handler(**msg.params)
                except Exception:
                    pass  # 通知失败静默（调用方不期待响应）
            return ""

        # 请求：执行 + 响应
        handler = self._methods.get(msg.method)
        if handler is None:
            return RpcResponse(id=msg.id, error={
                "code": ERROR_METHOD_NOT_FOUND,
                "message": f"方法不存在: {msg.method}"}).to_json()
        try:
            result = handler(**msg.params)
            return RpcResponse(id=msg.id, result=result).to_json()
        except RpcError as exc:
            return RpcResponse(id=msg.id, error={
                "code": exc.code, "message": exc.message}).to_json()
        except TypeError as exc:
            return RpcResponse(id=msg.id, error={
                "code": ERROR_INVALID_PARAMS,
                "message": f"参数错误: {exc}"}).to_json()
        except Exception as exc:
            return RpcResponse(id=msg.id, error={
                "code": ERROR_INTERNAL,
                "message": str(exc)}).to_json()
