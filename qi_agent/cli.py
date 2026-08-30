"""CLI 外壳（方案 2026-08-28-内核外壳分离）：只调 Gateway，不碰内核。

职责（外壳层——唯一读 stdin/渲染输出的地方）：
  - 读用户输入 → 调 Gateway 方法（session/create, message/send...）
  - 渲染通知（delta 流式 / approval 弹窗 / turn/end）
  - 命令处理（/status /resume /remember → 对应 Gateway/协议）

解耦：不 import 内核组件（build_runtime/AgentManager/AgentContext）——
  交互只在外壳，stdin 竞争根治（2026-08-28 并发排查）。

用法:
    uv run python -m qi_agent.cli            # 正常模式
    uv run python -m qi_agent.cli --debug    # 调试模式
"""

import argparse
import json

from qi_agent.gateway.gateway import Gateway

# ── 命令集合（2026-08-29 统一：全部 / 前缀——与业界 CLI 对齐）──────────
# 退出命令
EXIT_COMMANDS = {"/exit", "/quit", "/q"}
# 清理上下文（新会话）
CLEAR_COMMANDS = {"/clear"}
# 重要信息命令（/remember <内容>——sticky 挂 system prompt，永不裁剪）
REMEMBER_PREFIX = "/remember"
# 资源查看命令（2026-08-30 删除：/usage 是半成品指路牌——/status 已
# 完整展示状态 + token 统计，重复命令冗余）
# 上下文构成命令（/context 看占用构成）
CONTEXT_COMMANDS = {"/context"}
# 手动压缩命令（/compact 强制同步压缩）
COMPACT_COMMANDS = {"/compact"}
# 子任务命令（/delegate <目标> 手动拉起子任务）
DELEGATE_PREFIX = "/delegate"
# 状态命令（/status 看两级状态）
STATUS_COMMANDS = {"/status"}
# 终止命令（/stop 中断长任务）
STOP_COMMANDS = {"/stop"}
# 会话命令（/resume 恢复会话，/new 新建）
RESUME_PREFIX = "/resume"
NEW_COMMANDS = {"/new"}
# 记忆命令（/memory 查看跨会话记忆）
MEMORY_COMMANDS = {"/memory"}
# 帮助命令（/help 展示命令及用途——新增 2026-08-29）
HELP_COMMANDS = {"/help", "/h"}

# 命令帮助表（/help 展示——命令 + 用途）
HELP_TEXT = """可用命令：
  /exit            退出对话
  /clear           清空上下文（开始新会话）
  /new             新建会话
  /resume [id]     恢复历史会话（不带 id 列出可恢复的）
  /status          查看当前会话状态（轮数/消息/token）
  /stop            中断当前任务
  /remember <内容>  记住重要信息（会话内 + 跨会话）
  /memory          查看跨会话记忆
  /context         查看上下文构成
  /compact         手动压缩上下文
  /delegate <目标>  手动拉起子任务
  /help            显示本帮助
其他输入作为对话消息发送。"""


class CliShell:
    """CLI 外壳：Gateway 客户端（进程内 Phase 1）+ 渲染。"""

    def __init__(self, gateway: Gateway | None = None) -> None:
        self.gateway = gateway or Gateway()
        # 注册通知回调（审批弹窗/流式渲染——唯一读 stdin 的地方）
        self.gateway.shell_callback = self._on_notification
        self.session_id: str | None = None

    # ── 通知处理（网关 → 外壳）──────────────────────────────────────────
    def _on_notification(self, json_str: str) -> None:
        """处理网关通知（审批请求/流式增量/轮次结束）。"""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return
        method = data.get("method")
        params = data.get("params") or {}
        if method == "serverRequest/approval":
            self._handle_approval(params)
        elif method == "item/agentMessage/delta":
            print(params.get("text", ""), end="", flush=True)
        # turn/end 等其他通知暂不渲染（响应结果已含）

    def _handle_approval(self, params: dict) -> None:
        """审批弹窗（外壳唯一读 stdin 的交互点）。"""
        command = params.get("command", "")
        approval_id = params.get("approval_id", "")
        print(f"\n🤔 [审批] 执行命令 '{command}'？")
        choices = ["y", "n", "a"]
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        print("  0. 其他（自行输入）")
        while True:
            try:
                raw = input("请选择 (1-N 或 0 输入其他): ").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "n"  # 中断 = 拒绝（fail-closed）
            if raw in ("y", "1"):
                decision = "approve"
                break
            if raw in ("n", "2"):
                decision = "deny"
                break
            if raw in ("a", "3"):
                decision = "approve"  # a = 总是允许（简化：本次批准）
                break
            if raw == "0":
                decision = "deny"  # 其他 = 拒绝（简化）
                break
            print("无效选择，请重试")
        # 响应回网关（approval/respond）
        self.gateway._respond_approval(
            self.session_id or "", approval_id, decision)

    # ── 命令处理（协议方法调用）─────────────────────────────────────────
    def _handle_command(self, user_input: str) -> bool:
        """处理命令。返回 True = 已处理（不再当对话消息）。"""
        low = user_input.lower()
        if low in EXIT_COMMANDS:
            print("再见！")
            return True  # 主循环据此退出

        if low in HELP_COMMANDS:
            print(HELP_TEXT)
            return True

        if low in CLEAR_COMMANDS:
            # 清空会话（新会话——数据载体重建）
            self._new_session()
            print("已开始新会话（上下文已清空）！")
            return True

        if user_input.startswith(REMEMBER_PREFIX):
            content = user_input[len(REMEMBER_PREFIX):].strip()
            if content:
                from qi_agent.storage.memory_store import MemoryStore
                from qi_agent.context.sticky import remember

                remember(content)  # sticky（会话内）
                try:
                    MemoryStore().add_memory(content)
                except Exception:
                    pass
                print(f"已记住：{content}（会话内 + 跨会话）")
            else:
                print("用法：/remember <要记住的内容>")
            return True

        if low in STATUS_COMMANDS:
            ctx = self.gateway.manager.contexts.get(self.session_id or "")
            if ctx:
                print(f"[状态] 轮数: {ctx.turn} | 消息: {len(ctx.messages)}"
                      f" | status: {ctx.status.value} | phase: {ctx.phase.value}")
                u = ctx.usage
                print(f"[usage] prompt: {u['prompt_tokens']} | "
                      f"completion: {u['completion_tokens']} | "
                      f"total: {u['total_tokens']}")
            else:
                print("[状态] 无活动会话")
            return True

        if user_input.startswith(DELEGATE_PREFIX):
            # /delegate <目标>——拉起子 agent 并等结果（2026-08-30 修复：
            # 原异步拉起后立即返回（结果躺父 mailbox 没人展示）——现在
            # Gateway 同步等子完成，返回结果直接打印）
            goal = user_input[len(DELEGATE_PREFIX):].strip()
            if not goal:
                print("用法：/delegate <任务目标>")
                return True
            print(f"[delegate] 正在执行子任务：{goal}（请稍候...）")
            result = self.gateway._delegate(self.session_id or "", goal)
            r = result.get("result") or {}
            print(f"[delegate] 子任务 {result['session_id']} 完成：")
            if isinstance(r, dict):
                summary = r.get("summary", "")
                if summary:
                    print(f"  {summary[:2000]}")
                if r.get("error"):
                    print(f"  [错误] {r['error']}")
            else:
                print(f"  {str(r)[:2000]}")
            return True

        if low in CONTEXT_COMMANDS:
            ctx = self.gateway.manager.contexts.get(self.session_id or "")
            if ctx:
                print(f"[上下文构成] 消息数: {len(ctx.messages)} | "
                      f"轮数: {ctx.turn} | 状态: {ctx.status.value} | "
                      f"阶段: {ctx.phase.value}")
                for m in ctx.messages[-5:]:
                    print(f"  {m.get('role')}: {str(m.get('content'))[:60]}")
            else:
                print("[上下文] 无活动会话")
            return True

        if low in COMPACT_COMMANDS:
            # /compact——手动压缩（2026-08-30 补全：走 compressor 摘要压缩）
            ctx = self.gateway.manager.contexts.get(self.session_id or "")
            if ctx:
                from qi_agent.context.compressor import compress_messages
                summary = compress_messages(ctx.messages)
                print(f"[compact] 压缩完成（摘要已生成：{summary[:60]}...）")
            else:
                print("[compact] 无活动会话")
            return True

        if low in MEMORY_COMMANDS:
            from qi_agent.storage.memory_store import MemoryStore
            text = MemoryStore().read_memory()
            if text:
                print("=== 跨会话记忆（MEMORY.md + USER.md）===")
                print(text)
            else:
                print("暂无记忆。用 /remember <内容> 记录。")
            return True

        if low in STOP_COMMANDS:
            try:
                result = self.gateway._stop_session(self.session_id or "")
                print("[stop] 已请求中断当前任务" if result.get("stopped")
                      else "[stop] 无活动任务")
            except Exception as exc:
                print(f"[stop] {exc}")
            return True

        if user_input.startswith(RESUME_PREFIX):
            self._resume(user_input)
            return True

        if low in NEW_COMMANDS:
            self._new_session()
            return True

        if low in MEMORY_COMMANDS:
            from qi_agent.storage.memory_store import MemoryStore

            text = MemoryStore().read_memory()
            if text:
                print("=== 跨会话记忆（MEMORY.md + USER.md）===")
                print(text)
            else:
                print("暂无记忆。用 /remember <内容> 记录。")
            return True

        return False  # 不是命令 → 当对话消息

    def _new_session(self) -> None:
        result = self.gateway._create_session()
        self.session_id = result["session_id"]
        print(f"（新会话 {self.session_id}）")

    def _resume(self, user_input: str) -> None:
        from qi_agent.storage import get_storage

        store = get_storage()
        arg = user_input[len(RESUME_PREFIX):].strip()
        if not arg:
            sessions = store.list_sessions()
            if not sessions:
                print("暂无历史会话。")
            else:
                print("历史会话：")
                for s in sessions[:10]:
                    print(f"  {s['id']}  {s['title'] or '(无标题)'}")
            return
        sessions = store.list_sessions()
        match = next((s for s in sessions if s["id"].startswith(arg)), None)
        if match is None:
            print(f"未找到会话: {arg}")
            return
        result = self.gateway._resume_session(match["id"])
        self.session_id = result["session_id"]
        print(f"已恢复会话 {result['session_id']}"
              f"（{result['messages']} 条消息）")

    # ── 主循环 ───────────────────────────────────────────────────────────
    def run(self, debug: bool = False) -> None:
        """REPL 主循环（读 stdin → 调 Gateway → 渲染）。"""
        # 启动提示 + 历史会话提示（命令统一 / 前缀——2026-08-29）
        print("欢迎使用 qi-agent！（输入 /help 查看命令，/exit 退出，"
              "/clear 清理上下文。）")
        try:
            from qi_agent.storage import get_storage

            prev = get_storage().list_sessions()
            if prev:
                print(f"（发现 {len(prev)} 个历史会话，输入 /resume 可恢复）")
        except Exception:
            pass
        # 新建会话
        self._new_session()

        while True:
            try:
                user_input = input("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
            if not user_input:
                continue

            # 命令处理（先于对话消息）
            if self._handle_command(user_input):
                if user_input.lower() in EXIT_COMMANDS:
                    break
                continue

            # 对话消息 → Gateway
            try:
                if not debug:
                    print("agent> ", end="", flush=True)
                result = self.gateway._send_message(
                    self.session_id or "", user_input)
                if debug:
                    print(f"\n[回复] {result.get('reply', '')}")
                else:
                    print()  # 流式结束后换行
            except Exception as exc:
                print(f"[错误] 调用失败: {exc}")


def main(argv: list[str] | None = None) -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="qi-agent CLI")
    parser.add_argument("--debug", action="store_true",
                        help="调试模式（打印回复）")
    args = parser.parse_args(argv)
    shell = CliShell()
    shell.run(debug=args.debug)


if __name__ == "__main__":
    main()
