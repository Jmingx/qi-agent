"""CLI 入口：交互式 REPL（Read-Eval-Print Loop）。

用法:
    uv run python -m qi_agent.cli            # 正常模式
    uv run python -m qi_agent.cli --debug    # 调试模式（打印 LLM 交互链路）
"""

import argparse
import os

from dotenv import load_dotenv

from qi_agent.agent import Agent
from qi_agent.debugger import DebugLogger
from qi_agent.llm import LLMClient
from qi_agent.tools import get_time, read_file, shell  # noqa: F401  导入即注册内置工具

# 退出命令集合
EXIT_COMMANDS = {"exit", "quit", "退出", "q"}

# 清理上下文命令集合
CLEAR_COMMANDS = {"clear"}

def load_api_key() -> str:
    """从 .env 加载 DeepSeek API key，缺失时给出明确报错。"""
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "未找到 DEEPSEEK_API_KEY。\n"
            "请复制 .env.example 为 .env 并填入你的 DeepSeek API key。"
        )
    return api_key


def main(argv: list[str] | None = None) -> None:
    """启动 REPL 对话循环。

    Args:
        argv: 命令行参数列表（测试时注入，避免读取全局 sys.argv）。
              默认 None 表示从 sys.argv 解析（正常 CLI 使用）。
    """
    parser = argparse.ArgumentParser(description="qi-agent 命令行对话")
    parser.add_argument("--debug", action="store_true", help="打印 LLM 交互调试日志")
    args = parser.parse_args(argv)

    api_key = load_api_key()
    # --debug 时注入 DebugLogger，否则不传（行为与之前完全一致）
    logger = DebugLogger() if args.debug else None
    agent = Agent(LLMClient(api_key), logger=logger)

    print("欢迎使用 qi-agent！（输入 exit / quit / 退出 结束对话，clear 清理上下文。）")
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

        # 清理上下文命令
        if user_input.lower() in CLEAR_COMMANDS:
            print("已为您清理上下文！")
            agent.clear_context()
            continue

        try:
            # 流式前缀处理：
            # - 普通模式：打印 "agent> " 前缀，流式内容紧跟（打字机效果）
            # - --debug 模式：不打印前缀——日志框已展示完整链路（[USER]→[RESP]），
            #   流式文本单独一行输出，避免前缀与日志框粘连错位
            if logger is None:
                print("agent> ", end="", flush=True)
            reply = agent.chat(
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


if __name__ == "__main__":
    main()
