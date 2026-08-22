"""交互抽象层：工具与具体交互形态（终端/未来 Web UI 等）分离。

设计（方案 2026-08-22-工具三件套，对齐 Hermes clarify_tool）：
- Hermes 明确"实际交互逻辑在平台层（cli.py / gateway），工具层只定义
  schema、校验和薄分发器，委托平台回调"——为后续其他形态 CLI 预留
  拓展空间的正确方式就是抽象 + 注入
- InteractionProvider：交互协议（ask = 向用户提问并等待回答）
- TerminalInteraction：本期实现（终端 input + 选项编号选择）
- 未来：WebInteraction（HTTP 轮询）/ GuiInteraction / 消息平台 ——
  换实现注册即可，clarify 等工具零改动
- fail-safe：未注册 provider / 非 tty → InteractionUnavailableError
  （对齐 approval fail-closed 哲学：无交互环境不挂死）
"""

import sys
from abc import ABC, abstractmethod

# 全局当前 provider（CLI 启动时注入；未注册 = fail-safe）
_PROVIDER: "InteractionProvider | None" = None


class InteractionUnavailableError(RuntimeError):
    """交互不可用（未注册 provider / 非 tty / 输入中断）。"""


class InteractionProvider(ABC):
    """交互提供者协议：向用户提问并等待回答。"""

    @abstractmethod
    def ask(self, question: str, choices: list[str] | None = None,
            timeout: float | None = 60.0) -> str:
        """向用户提问，返回回答。

        Args:
            question: 问题文本
            choices: 选项列表（≤4，None = 开放式提问）
            timeout: 等待秒数（None = 无限等待）

        Raises:
            InteractionUnavailableError: 当前环境无法交互
        """


class TerminalInteraction(InteractionProvider):
    """终端交互：input() 自由文本 + 选项编号选择（CLI 默认实现）。"""

    def ask(self, question: str, choices: list[str] | None = None,
            timeout: float | None = 60.0) -> str:
        # 非 tty（评测/管道/重定向）→ fail-safe，不挂死
        if not sys.stdin.isatty():
            raise InteractionUnavailableError("stdin 非终端（评测/管道环境），无法交互")
        # 注意：timeout 参数接受但不强制超时——交互式终端由用户控制节奏，
        # 强制超时在 Windows 上无可靠实现（signal.alarm 不可用）；
        # 评测等需要超时的场景走非 tty fail-safe 路径（不挂死）
        if choices:
            print(f"🤔 {question}")
            for i, c in enumerate(choices, 1):
                print(f"  {i}. {c}")
            print("  0. 其他（自行输入）")
            while True:
                try:
                    raw = input("请选择 (1-N 或 0 输入其他): ").strip()
                except (EOFError, KeyboardInterrupt) as exc:
                    raise InteractionUnavailableError("输入中断") from exc
                if raw.isdigit() and 1 <= int(raw) <= len(choices):
                    return choices[int(raw) - 1]
                if raw.isdigit() and int(raw) == 0:
                    break  # 用户选择"其他" → 走下方自由输入
                print("无效选择，请重试")
        try:
            answer = input(f"🤔 {question}: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise InteractionUnavailableError("输入中断") from exc
        if not answer:
            raise InteractionUnavailableError("回答为空")
        return answer


def set_interaction_provider(provider: "InteractionProvider | None") -> None:
    """注册交互提供者（CLI 启动时注入；None 清除）。

    未来接入 Web/GUI：实现 InteractionProvider 后在此注册，工具零改动。
    """
    global _PROVIDER
    _PROVIDER = provider


def get_interaction_provider() -> "InteractionProvider | None":
    """当前注册的交互提供者（未注册返回 None）。"""
    return _PROVIDER


def ask_user(question: str, choices: list[str] | None = None,
             timeout: float | None = 60.0) -> str:
    """工具/插件统一入口：向用户提问。

    Args:
        question: 问题文本
        choices: 选项列表（≤4，None = 开放式）
        timeout: 等待秒数（None = 无限）

    Raises:
        InteractionUnavailableError: 未注册 provider（无交互环境）
    """
    provider = _PROVIDER
    if provider is None:
        raise InteractionUnavailableError("交互提供者未注册（无交互环境）")
    return provider.ask(question, choices=choices, timeout=timeout)
