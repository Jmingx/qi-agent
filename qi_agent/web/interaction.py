"""Web 交互实现：ask_user → 网关审批桥 → 浏览器弹框（方案 2026-09-13 §3.5）。

职责边界：本实现只做"把提问转成网关审批请求 + 把答案转回决策层"，
**不承担放行决策**——超时 / 缺会话 / 网关异常一律抛
InteractionUnavailableError（网关异常原样上抛，由调用方兜底），
放行语义仍由 approval_gate / clarify 的既有 fail-closed 逻辑收口。

会话身份由调用方经 `meta["session"]` 显式传入（插件读 bus.context_id），
provider 自身不猜上下文——多标签 / 后台会话并存时不会串线。
"""

from typing import Any

from qi_agent.interaction import (
    InteractionOption,
    InteractionUnavailableError,
    InteractionProvider,
    normalize_options,
)

# 网关审批桥的默认等待（与 gateway.APPROVAL_TIMEOUT 对齐）
DEFAULT_TIMEOUT = 60.0


class WebInteraction(InteractionProvider):
    """把交互请求交给网关审批桥（serve 进程注册，全局单例）。"""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    def ask(self, question: str, options: "list[InteractionOption] | None" = None,
            timeout: float | None = DEFAULT_TIMEOUT, *,
            meta: dict | None = None) -> str:
        """提问 → 通知浏览器弹框 → 返回所选 value；超时/缺会话抛不可用。"""
        context = dict(meta or {})
        session_id = str(context.get("session", "") or "")
        if not session_id:
            # 无会话上下文：宁可 fail-closed 也不能猜（否则弹到别人会话上）
            raise InteractionUnavailableError(
                "缺少会话上下文（meta.session）——无法定位弹框归属"
            )
        payload = [option.to_dict() for option in normalize_options(options)]
        chosen = self._gateway.request_approval(
            session_id,
            question,
            payload,
            meta=context,
            timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
        )
        if chosen is None:
            # 网关等待超时 / 无人响应 → 交互不可用（决策层按拒绝处理）
            raise InteractionUnavailableError("审批超时或未响应（视为拒绝）")
        return str(chosen)
