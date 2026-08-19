"""工具调用统计插件：监听 agent/* 事件，汇总工具使用情况。

对应 TODO tool-calling.md「工具调用统计」（P1）。
设计（方案 docs/plans/2026-08-18-事件化改造方案.md 3.3）：
- install(bus) 统一入口：注册监听器（将来容器化时接口不变）
- tool-call 用 priority=100：先于审批类插件记录（被拦截的调用也算尝试）
- duration 由 agent.py 测量并随 tool-result 事件传入（单一职责：
  agent 提供数据，插件只消费）
"""

import time
from dataclasses import dataclass

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin

# 判定失败的输出前缀（与 execute_tool 的错误约定一致）
_FAILURE_PREFIXES = ("[工具错误]", "[参数错误]")


@dataclass
class ToolStats:
    """单个工具的统计信息。"""

    name: str
    calls: int = 0
    total_seconds: float = 0.0
    failures: int = 0


class ToolStatsPlugin:
    """统计插件：收集并汇总工具调用数据。"""

    def __init__(self) -> None:
        self._stats: dict[str, ToolStats] = {}
        self._call_start: dict[str, float] = {}  # tool_call 名 → 开始时间
        # 注：dict 记录开始时间仅为兜底（多工具并行时 key 冲突）；
        # 正常路径直接用 tool-result 事件携带的 duration

    def install(self, bus: EventBus) -> None:
        """注册所有监听器（插件统一入口）。"""
        bus.on("agent/tool-call", self._on_tool_call, priority=100)
        bus.on("agent/tool-result", self._on_tool_result)

    def _on_tool_call(self, name: str, **_) -> None:
        """工具调用前：登记开始时间（兜底用）。"""
        self._call_start[name] = time.perf_counter()

    def _on_tool_result(self, name: str, output: str, duration: float, **_) -> None:
        """工具执行后：累加统计。"""
        stat = self._stats.setdefault(name, ToolStats(name=name))
        stat.calls += 1
        stat.total_seconds += duration
        if output.startswith(_FAILURE_PREFIXES):
            stat.failures += 1

    def report(self) -> str:
        """生成汇总报告文本（会话结束时打印）。"""
        if not self._stats:
            return "[工具统计] 本次会话未调用任何工具"
        lines = ["[工具统计] 本次会话工具调用汇总:"]
        for stat in self._stats.values():
            lines.append(
                f"  {stat.name}: {stat.calls} 次, "
                f"总耗时 {stat.total_seconds:.3f}s, 失败 {stat.failures}"
            )
        return "\n".join(lines)


# 自注册（方案 v0.4.9）：观测类插件默认关，配置文件或 --stats 快捷开关启用
register_plugin(
    name="tool_stats",
    factory=ToolStatsPlugin,
    description="工具调用统计（次数/耗时/失败数），--stats 临时启用",
    default_enabled=False,
)
