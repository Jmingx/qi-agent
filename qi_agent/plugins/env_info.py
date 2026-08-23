"""环境信息插件：向模型注入运行环境上下文（pre-step 幂等注入）。

设计（方案 docs/plans/2026-08-19-安全与环境增强方案.md）：
- 背景：真实对话中模型尝试 Linux 命令（pwd/ls）失败白烧 4 轮——
  环境错配（白名单是 Linux 向，执行环境是 Windows cmd）
- 形态：上下文注入插件（非监听型）——pre-step waterfall 注入 system 消息
- 幂等：marker 前缀检查，防止 pre-step 每 step 触发导致重复堆积
- 静态性：环境构造时检测一次（会话中不变，合理）
"""

import platform

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin

# 注入消息的标识前缀（幂等检查用）
_MARKER = "[环境信息]"


class EnvInfoPlugin:
    """注入运行环境信息：模型少踩环境坑。"""

    def __init__(self, config: dict | None = None) -> None:
        self._info = self._detect()  # 构造时检测一次（静态信息）

    def _detect(self) -> str:
        """运行时检测环境，生成注入文本。"""
        system = platform.system()
        lines = [f"{_MARKER} 运行环境: {system} {platform.release()}"]
        if system == "Windows":
            lines.append("shell: cmd.exe（Windows 命令），非 bash")
            lines.append("可用只读命令: dir, type, echo, where, whoami, date, time")
            lines.append("路径风格: Windows（C:\\...），目录分隔符为 \\")
        else:
            lines.append("shell: bash")
            lines.append("可用只读命令: pwd, ls, cat, echo, which, whoami, date")
        return "\n".join(lines)

    def install(self, bus: EventBus) -> None:
        """注册监听器：注入类插件默认 priority=0（不参与决策链）。"""
        bus.on("agent/pre-step", self._inject)

    def _inject(self, messages: list[dict], **_) -> list[dict]:
        """幂等注入：marker 已存在则跳过（pre-step 每 step 都触发）。"""
        if any(m.get("content", "").startswith(_MARKER) for m in messages):
            return messages  # 已注入过 → 原样返回
        return [{"role": "system", "content": self._info}] + messages


# 自注册：环境信息对模型有益且零风险 → 默认开
register_plugin(
    name="env_info",
    factory=EnvInfoPlugin,
    description="注入运行环境信息（OS/shell/可用命令），模型少踩环境坑",
    default_enabled=True,
)
