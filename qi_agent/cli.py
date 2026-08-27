"""CLI 入口：交互式 REPL（Read-Eval-Print Loop）。

用法:
    uv run python -m qi_agent.cli            # 正常模式
    uv run python -m qi_agent.cli --debug    # 调试模式（打印 LLM 交互链路）
"""

import argparse

from qi_agent.agents.factory import build_runtime
from qi_agent.context.sticky import remember
from qi_agent.interaction import TerminalInteraction, set_interaction_provider
from qi_agent.tools.builtin import (  # noqa: F401  导入即注册内置工具
    get_time, read_file, run_python, shell,
)

# 退出命令集合
EXIT_COMMANDS = {"exit", "quit", "退出", "q"}

# 清理上下文命令集合
CLEAR_COMMANDS = {"clear"}

# 重要信息命令前缀（阶段 B3，方案 2026-08-22）：/remember <内容>
# sticky 挂 system prompt——用户要求保留的信息永不裁剪
REMEMBER_PREFIX = "/remember"

# 资源查看命令集合（2026-08-21 交互调整：命令式查看资源消耗，不再每轮输出）
USAGE_COMMANDS = {"usage", "资源"}

# 上下文构成命令（阶段 C 收尾，方案 2026-08-23）：/context 看占用构成
CONTEXT_COMMANDS = {"context", "上下文"}

# 手动压缩命令（阶段 C 收尾）：/compact 强制同步压缩
COMPACT_COMMANDS = {"compact"}

# 子任务命令（subagent 方案 2026-08-23）：/delegate <目标> 手动拉起子任务
DELEGATE_PREFIX = "/delegate"

# 状态命令（方案 2026-08-24-AgentManager统一控制台）：/status 看两级状态
STATUS_COMMANDS = {"status", "状态"}

# 终止命令（方案 2026-08-24-AgentManager统一控制台）：/stop 中断长任务
STOP_COMMANDS = {"stop", "停止"}

# 会话命令（方案 2026-08-26-会话持久化）：/resume 恢复会话，/new 新建
RESUME_PREFIX = "/resume"
NEW_COMMANDS = {"new", "/new", "新会话"}

# 记忆命令（方案 2026-08-26-记忆系统）：/memory 查看跨会话记忆
MEMORY_COMMANDS = {"memory", "/memory", "记忆"}


def _print_plugin_reports(installed_plugins: list) -> None:
    """打印所有带 report() 的插件汇总（约定：观测类插件提供 report 方法）。

    会话退出时与 usage 命令共用——一次查看全会话统计。
    """
    for plugin in installed_plugins:
        report = getattr(plugin, "report", None)
        if report:
            print(report())


def main(argv: list[str] | None = None) -> None:
    """启动 REPL 对话循环。

    Args:
        argv: 命令行参数列表（测试时注入，避免读取全局 sys.argv）。
              默认 None 表示从 sys.argv 解析（正常 CLI 使用）。
    """
    parser = argparse.ArgumentParser(description="qi-agent 命令行对话")
    parser.add_argument("--debug", action="store_true", help="打印 LLM 交互调试日志")
    parser.add_argument("--stats", action="store_true", help="会话结束打印工具调用统计")
    args = parser.parse_args(argv)

    # 交互注入（v0.4.26）：clarify 等交互工具注册终端实现——未来 Web/GUI
    # 换 InteractionProvider 实现即可，工具零改动（交互与工具分离架构）
    set_interaction_provider(TerminalInteraction())

    # 构建运行时（真实形态）：manager + context + 插件装配收敛在 agent_factory
    # RuntimeBundle（方案 2026-08-24）：manager + context_id（ctx_ 前缀）
    # 执行权归还 Manager（用户拍板）：CLI 不持有 agent，只调 manager.run——
    # agent 生命周期在 pool（即用即弃），manager 不感知具体执行者
    runtime = build_runtime(debug=args.debug, stats=args.stats)
    manager = runtime.manager
    context_id = runtime.context_id
    installed_plugins = runtime.installed

    print("欢迎使用 qi-agent！（输入 exit / quit / 退出 结束对话，clear 清理上下文。）")

    # 崩溃恢复提示（方案 2026-08-26）：有历史会话时提示可恢复
    # （write-behind 持久化——上次会话已落盘，/resume 可续聊）
    try:
        from qi_agent.storage import get_storage

        prev_sessions = get_storage().list_sessions()
        if prev_sessions:
            print(f"（发现 {len(prev_sessions)} 个历史会话，输入 /resume 可恢复）")
    except Exception:
        pass  # 存储不可用不影响启动

    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+D / Ctrl+C：优雅退出
            print("\n再见！")
            break

        # 空输入：跳过，不浪费一次 API 调用
        if not user_input:
            continue

        # 退出命令
        if user_input.lower() in EXIT_COMMANDS:
            print("再见！")
            break

        # 清理上下文命令（2026-08-24 用户拍板：clear 是数据载体重置，
        # 挪到 context——agent 无状态，没有"清自己"的概念）
        if user_input.lower() in CLEAR_COMMANDS:
            print("已为您清理上下文！")
            runtime.get_context().reset_session()
            continue

        # 会话命令（方案 2026-08-26）：/resume 恢复 /new 新建
        if user_input.lower() in NEW_COMMANDS:
            # 当前会话已持久化（persist=True 自动落盘）——直接开新 context
            from qi_agent.context.context import AgentContext

            new_ctx = AgentContext(persist=True,
                                   events=runtime.get_context().events)
            manager.unregister(runtime.context_id)
            manager.register(new_ctx, role="main")
            runtime.context_id = new_ctx.id  # 同一运行时换 context（数据载体）
            print("已开始新会话！")
            continue

        if user_input.startswith(RESUME_PREFIX):
            from qi_agent.storage import get_storage

            store = get_storage()
            arg = user_input[len(RESUME_PREFIX):].strip()
            if not arg:
                # 列出会话
                sessions = store.list_sessions()
                if not sessions:
                    print("暂无历史会话。")
                else:
                    print("历史会话：")
                    for s in sessions[:10]:
                        print(f"  {s['id']}  {s['title'] or '(无标题)'}")
                continue
            # 恢复指定会话（按 id 前缀匹配）
            sessions = store.list_sessions()
            match = next((s for s in sessions if s["id"].startswith(arg)), None)
            if match is None:
                print(f"未找到会话: {arg}")
                continue
            loaded = store.load_session(match["id"])
            if loaded is None:
                print(f"会话数据为空: {arg}")
                continue
            # 用原 context_id 新建 context + 恢复数据
            from qi_agent.context.context import AgentContext

            new_ctx = AgentContext(persist=True, context_id=match["id"],
                                   events=runtime.get_context().events)
            new_ctx.messages = loaded["messages"]
            new_ctx.turn = loaded["turn"]
            new_ctx.usage = loaded["usage"]
            new_ctx.system_prompt = (
                new_ctx.messages[0]["content"] if new_ctx.messages
                and new_ctx.messages[0]["role"] == "system" else "")
            manager.unregister(runtime.context_id)
            manager.register(new_ctx, role="main")
            runtime.context_id = new_ctx.id  # 同一运行时换 context（数据载体）
            print(f"已恢复会话 {match['id']}（{len(new_ctx.messages)} 条消息）")
            continue

        # 重要信息命令（阶段 B3 + 方案 2026-08-26）：
        # /remember <内容> → sticky（会话内永不裁剪）+ MEMORY.md（跨会话）
        if user_input.startswith(REMEMBER_PREFIX):
            content = user_input[len(REMEMBER_PREFIX):].strip()
            if content:
                remember(content)  # sticky（会话内）
                try:
                    from qi_agent.storage.memory_store import MemoryStore

                    MemoryStore().add_memory(content)
                except Exception:
                    pass  # 存储不可用 → 只留 sticky
                print(f"已记住：{content}（会话内 + 跨会话）")
            else:
                print("用法：/remember <要记住的内容>")
            continue

        # 记忆查看命令（方案 2026-08-26）：/memory 查看跨会话记忆
        if user_input.lower() in MEMORY_COMMANDS:
            try:
                from qi_agent.storage.memory_store import MemoryStore

                text = MemoryStore().read_memory()
                if text:
                    print("=== 跨会话记忆（MEMORY.md + USER.md）===")
                    print(text)
                else:
                    print("暂无记忆。用 /remember <内容> 记录。")
            except Exception as exc:
                print(f"[memory] 读取失败: {exc}")
            continue

        # 资源消耗命令（2026-08-21：查看当前累计，不消耗 LLM 调用）
        if user_input.lower() in USAGE_COMMANDS:
            _print_plugin_reports(installed_plugins)
            continue

        # 上下文构成命令（阶段 C 收尾）：/context 显示占用构成（估算分段
        # + 真实 usage 累计——分工：估算=预测展示，真实=统计/事实窗口）
        # 数据走 context（数据载体），不直连 agent（换执行者不受影响）
        if user_input.lower() in CONTEXT_COMMANDS:
            from qi_agent.context.breakdown import (
                compute_breakdown,
                format_breakdown,
            )
            from qi_agent.tools.registry import get_tool_schemas

            ctx = runtime.get_context()
            print(format_breakdown(compute_breakdown(
                ctx.messages, get_tool_schemas())))
            u = ctx.usage
            print(
                f"[用量] 累计 {u['total_tokens']} tokens"
                f"（prompt {u['prompt_tokens']} + completion {u['completion_tokens']}）"
            )
            continue

        # 手动压缩命令（阶段 C 收尾）：/compact 强制同步压缩当前消息
        if user_input.lower() in COMPACT_COMMANDS:
            from qi_agent.plugins.builtin.context_manager import (
                ContextManagerPlugin,
            )

            cm = next(
                (p for p in installed_plugins
                 if isinstance(p, ContextManagerPlugin)), None)
            if cm is None:
                print("[compact] 上下文管理插件未启用")
            else:
                ctx = runtime.get_context()
                before = len(ctx.messages)
                new_msgs, summary = cm.compact_now(ctx.messages)
                ctx.messages = new_msgs
                print(
                    f"[compact] 压缩完成：{before} → {len(new_msgs)} 条消息"
                )
                if summary:
                    print(f"摘要：{summary[:200]}")
                else:
                    print("（无可压缩历史）")
            continue

        # 子任务命令（subagent 方案 2026-08-23）：/delegate <目标>
        # 手动拉起子任务（用户主导，不走主 agent 工具循环——编排双入口之一）
        if user_input.startswith(DELEGATE_PREFIX):
            goal = user_input[len(DELEGATE_PREFIX):].strip()
            if not goal:
                print(
                    "用法：/delegate <子任务目标>\n"
                    "示例：/delegate 调研 docs/ 目录下所有方案文档的核心决策"
                )
            else:
                from qi_agent.tools.builtin.delegate_task import delegate_task

                print(f"[delegate] 子任务启动：{goal[:80]}")
                ctx = runtime.get_context()
                output = delegate_task(
                    goal=goal,
                    context=(
                        "主对话上下文：当前项目 qi-agent（Python agent 框架）。"
                        f"最近用户输入：{ctx.messages[-1].get('content', '')[:200]}"
                        if ctx.messages else "主对话上下文：当前项目 qi-agent。"
                    ),
                )
                try:
                    import json

                    data = json.loads(output)
                    print(f"[delegate] 状态：{data.get('status')}")
                    if data.get("summary"):
                        print(f"总结：{data['summary']}")
                    if data.get("artifacts"):
                        print(f"产出：{', '.join(data['artifacts'])}")
                    if data.get("error"):
                        print(f"错误：{data['error']}")
                    if data.get("question"):
                        print(f"询问：{data['question']}")
                except (json.JSONDecodeError, ValueError):
                    print(output)
            continue

        # 状态命令（方案 2026-08-24-AgentManager统一控制台）：/status
        # 显示两级状态机（会话级 status + 循环级 phase）+ usage/turn/消息数
        if user_input.lower() in STATUS_COMMANDS:
            status = manager.poll(context_id)
            ctx = runtime.get_context()
            phase = ctx.phase.value
            u = ctx.usage
            print(
                f"[状态] 会话级: {status.value} | 循环级: {phase}\n"
                f"[状态] 轮数: {ctx.turn} | 消息: {len(ctx.messages)}\n"
                f"[用量] 累计 {u['total_tokens']} tokens"
                f"（prompt {u['prompt_tokens']} + completion {u['completion_tokens']}）"
            )
            continue

        # 终止命令（方案 2026-08-24-AgentManager统一控制台）：/stop
        # 中断当前长任务（下轮生效——chat 阻塞在 LLM 调用时 v2 升级实时中断）
        if user_input.lower() in STOP_COMMANDS:
            stopped = manager.stop(context_id)
            print("[stop] 已请求中断当前任务（下轮生效）" if stopped
                  else "[stop] 主 agent 不在控制台")
            continue

        try:
            # 流式前缀处理：
            # - 普通模式：打印 "agent> " 前缀，流式内容紧跟（打字机效果）
            # - --debug 模式：不打印前缀——日志插件已展示完整链路
            #   （[USER]→[RESP]），流式文本单独一行输出，避免前缀粘连
            if not args.debug:
                print("agent> ", end="", flush=True)
            # 执行权归还 Manager（方案 2026-08-24）：CLI 只调 manager.run，
            # 不持有 agent——执行者由 manager 内部经 pool 即用即弃
            reply = manager.run(
                context_id,
                user_input,
                # 流式回调：逐块打印（flush=True 强制立即输出，否则被缓冲）
                stream_callback=lambda delta: print(delta, end="", flush=True),
            )
            print()  # 流式结束后换行（打字机效果完整）
            _ = reply  # 完整文本已在流式中显示，无需重复打印
        except KeyboardInterrupt:
            # Ctrl+C 在等待 API 响应时按下：优雅退出（KeyboardInterrupt
            # 继承 BaseException，不会被 except Exception 捕获，需单独处理）
            print("\n[已中断] 再见！")
            break
        except Exception as exc:  # API 失败不崩溃，继续对话
            print(f"[错误] 调用失败: {exc}")

    # 会话结束（while break 后）：打印所有带 report() 的插件汇总
    # （约定：观测类插件提供 report() 方法；与 usage 命令共用 _print_plugin_reports）
    _print_plugin_reports(installed_plugins)


if __name__ == "__main__":
    main()
