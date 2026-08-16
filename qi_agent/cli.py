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
from qi_agent.tools import builtin  # noqa: F401  导入即注册内置工具

# 退出命令集合
EXIT_COMMANDS = {"exit", "quit", "退出", "q"}


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


def main() -> None:
    """启动 REPL 对话循环。"""
    parser = argparse.ArgumentParser(description="qi-agent 命令行对话")
    parser.add_argument("--debug", action="store_true", help="打印 LLM 交互调试日志")
    args = parser.parse_args()

    api_key = load_api_key()
    # --debug 时注入 DebugLogger，否则不传（行为与之前完全一致）
    logger = DebugLogger() if args.debug else None
    agent = Agent(LLMClient(api_key), logger=logger)

    print("欢迎使用 qi-agent！（输入 exit / quit / 退出 结束对话）")
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

        try:
            reply = agent.chat(user_input)
            print(f"agent> {reply}")
        except Exception as exc:  # API 失败不崩溃，继续对话
            print(f"[错误] 调用失败: {exc}")


if __name__ == "__main__":
    main()
