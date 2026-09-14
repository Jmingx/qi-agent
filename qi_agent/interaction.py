"""交互抽象层：工具/插件与具体交互形态（终端 / Web UI）分离。

设计（方案 2026-09-13-Web权限审批弹框方案 §3.2）：
- **InteractionOption**：选项 = (value 机器语义, label 展示文案, tone 渲染提示)。
  决策层给语义（once/session/deny），外壳只管渲染——跨进程传递不再用
  CLI 惯用词（y/n/a），前端也不必硬编码映射。
- **InteractionProvider**：交互协议（ask = 向用户提问并等待回答）。
- **TerminalInteraction**：终端实现（编号选择 + y/n/a 语义别名）。
- **WebInteraction**（qi_agent/web/interaction.py）：经网关审批桥弹到浏览器。
- **meta**：结构化上下文（session/tool/code/command），图形外壳渲染依据；
  终端实现忽略它（终端只打 label）。
- fail-safe 不变：未注册 provider / 非 tty → InteractionUnavailableError
  （对齐 approval fail-closed 哲学：无交互环境不挂死、不放行）。
"""

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# 全局当前 provider（外壳启动时注入；未注册 = fail-safe）
_PROVIDER: "InteractionProvider | None" = None

# 终端语义别名：CLI 老习惯（y/n/a）→ 规范化选项值。
# 映射集中在本实现——跨外壳契约只认 value（once/session/deny）。
_TERMINAL_ALIASES = {"y": "once", "n": "deny", "a": "session"}


class InteractionUnavailableError(RuntimeError):
    """交互不可用（未注册 provider / 非 tty / 超时 / 无会话上下文）。"""


@dataclass(frozen=True)
class InteractionOption:
    """一个可选项：机器语义 + 展示文案 + 渲染提示。

    Attributes:
        value: 机器语义值（审批档：once|session|deny；澄清：任意字符串）
        label: 展示文案（空 → 回退 value，避免前端渲染空按钮）
        tone: 渲染提示（primary|danger|muted，空 = 默认样式）
    """

    value: str
    label: str = ""
    tone: str = ""

    def __post_init__(self) -> None:
        # frozen dataclass：回退文案要用 object.__setattr__ 绕过不可变保护
        if not self.label:
            object.__setattr__(self, "label", self.value)

    def to_dict(self) -> dict[str, str]:
        """转成可跨进程传递的 dict（网关通知用）。"""
        return {"value": self.value, "label": self.label, "tone": self.tone}


def normalize_options(options: "list[Any] | None") -> list[InteractionOption]:
    """把调用方传的选项统一成 InteractionOption 列表。

    兼容三种形态（老调用方零改动）：None / list[str] / list[InteractionOption]，
    另接受 dict（跨层边界：网关通知回读）——统一在这里收口。
    """
    result: list[InteractionOption] = []
    for item in options or []:
        if isinstance(item, InteractionOption):
            result.append(item)
        elif isinstance(item, dict):
            result.append(InteractionOption(
                value=str(item.get("value", "")),
                label=str(item.get("label", "")),
                tone=str(item.get("tone", "")),
            ))
        else:
            result.append(InteractionOption(value=str(item)))
    return result


class InteractionProvider(ABC):
    """交互提供者协议：向用户提问并等待回答。"""

    @abstractmethod
    def ask(self, question: str, options: "list[InteractionOption] | None" = None,
            timeout: float | None = 60.0, *, meta: dict | None = None) -> str:
        """向用户提问，返回所选项的 value（开放式提问返回自由文本）。

        Args:
            question: 问题文本
            options: 选项（None = 开放式提问）
            timeout: 等待秒数（None = 无限等待）
            meta: 结构化上下文（session/tool/code/command；图形外壳渲染用）

        Raises:
            InteractionUnavailableError: 当前环境无法交互（fail-safe）
        """


class TerminalInteraction(InteractionProvider):
    """终端交互：编号选择（展示 label）+ 自由文本 + y/n/a 语义别名。"""

    def ask(self, question: str, options: "list[InteractionOption] | None" = None,
            timeout: float | None = 60.0, *, meta: dict | None = None) -> str:
        # 非 tty（评测/管道/重定向）→ fail-safe，不挂死
        if not sys.stdin.isatty():
            raise InteractionUnavailableError("stdin 非终端（评测/管道环境），无法交互")
        # 注意：timeout 接受但不强制（交互式终端由用户控制节奏；Windows 无可靠
        # 强制超时实现）——需要超时的场景走非 tty fail-safe 路径 / Web 外壳。
        choices = normalize_options(options)
        if choices:
            print(f"🤔 {question}")
            for i, option in enumerate(choices, 1):
                print(f"  {i}. {option.label}")
            print("  0. 其他（自行输入）")
            while True:
                try:
                    raw = input("请选择 (1-N 或 0 输入其他): ").strip()
                except (EOFError, KeyboardInterrupt) as exc:
                    raise InteractionUnavailableError("输入中断") from exc
                if raw.isdigit() and 1 <= int(raw) <= len(choices):
                    return choices[int(raw) - 1].value
                if raw.isdigit() and int(raw) == 0:
                    break  # 用户选择"其他" → 走下方自由输入
                picked = self._match_semantic(raw, choices)
                if picked is not None:
                    return picked
                print("无效选择，请重试")
        try:
            answer = input(f"🤔 {question}: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise InteractionUnavailableError("输入中断") from exc
        if not answer:
            raise InteractionUnavailableError("回答为空")
        return answer

    @staticmethod
    def _match_semantic(raw: str, choices: list[InteractionOption]) -> str | None:
        """语义输入匹配：y/n/a 别名 或 直接输入 value。

        别名只在对应选项**存在**时生效（沙箱档没有 session → 输入 a 无效），
        避免用户以为"总是允许"生效了实际被忽略。
        """
        values = [option.value for option in choices]
        alias = _TERMINAL_ALIASES.get(raw.lower())
        if alias is not None and alias in values:
            return alias
        if raw in values:
            return raw
        return None


def set_interaction_provider(provider: "InteractionProvider | None") -> None:
    """注册交互提供者（外壳启动时注入；None 清除）。"""
    global _PROVIDER
    _PROVIDER = provider


def get_interaction_provider() -> "InteractionProvider | None":
    """当前注册的交互提供者（未注册返回 None）。"""
    return _PROVIDER


def ask_user(question: str, options: "list[Any] | None" = None,
             timeout: float | None = 60.0, *, meta: dict | None = None) -> str:
    """工具/插件统一入口：向用户提问。

    Args:
        question: 问题文本
        options: 选项（None = 开放式；接受 list[str] / list[InteractionOption]）
        timeout: 等待秒数（None = 无限）
        meta: 结构化上下文（图形外壳渲染用，终端忽略）

    Raises:
        InteractionUnavailableError: 未注册 provider（无交互环境）
    """
    provider = _PROVIDER
    if provider is None:
        raise InteractionUnavailableError("交互提供者未注册（无交互环境）")
    return provider.ask(question, normalize_options(options), timeout=timeout, meta=meta)
