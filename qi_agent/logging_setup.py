"""统一日志基础设施（2026-08-30：run/message/events 日志共用工厂）。

设计（对齐 gateway/protocol.py 的 rpc.log 模式）：
  每类日志一个 logger（qi_agent.run / qi_agent.message / qi_agent.events）
  + FileHandler 写 ~/.qi-agent/logs/<name>.log
  → 幂等（logger 只配一次 handler——重复调用不叠加）
  → 默认只写文件（不污染 CLI 输出）

日志目录：
  ~/.qi-agent/logs/
    rpc.log       RPC 接口日志（gateway/protocol.py）
    run.log       执行日志（AgentManager.run 生命周期）
    message.log   邮局消息日志（Dispatcher 收发全链路）
    events.log    事件总线日志（EventBus emit/on）
"""

import logging
import os

_LOG_DIR = os.path.join(os.path.expanduser("~"), ".qi-agent", "logs")


def get_logger(name: str, filename: str) -> logging.Logger:
    """获取日志器（写 ~/.qi-agent/logs/<filename>）。

    幂等：logger 只配一次 handler（重复调用不叠加——测试间清理靠
    测试自身处理）。默认只写文件（不污染 CLI 交互输出）。
    """
    logger = logging.getLogger(f"qi_agent.{name}")
    if not logger.handlers:  # 幂等（只配一次）
        os.makedirs(_LOG_DIR, exist_ok=True)
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(
            os.path.join(_LOG_DIR, filename), encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    return logger


def get_run_logger() -> logging.Logger:
    """执行日志（run.log——AgentManager.run 生命周期）。"""
    return get_logger("run", "run.log")


def get_message_logger() -> logging.Logger:
    """邮局消息日志（message.log——Dispatcher 收发全链路）。"""
    return get_logger("message", "message.log")


def get_events_logger() -> logging.Logger:
    """事件总线日志（events.log——EventBus emit/on）。"""
    return get_logger("events", "events.log")
