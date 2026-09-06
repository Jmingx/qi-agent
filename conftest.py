"""pytest 环境兜底。

主要用途是给 pytest 分配一个每次运行都唯一的 basetemp，避免系统临时目录
和旧的残留目录把 `tmp_path` fixture 搞坏。
（2026-09-02：Codex 会话误删本文件后由 Hermes 按原功能重写恢复。）
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    # 不把 pytest 临时目录放进仓库根下的 tmp/，否则 Windows ACL / 残留
    # symlink 会让 cleanup_dead_symlinks 直接炸掉。
    # 本机 Temp 目录被安全软件接管时，pytest 在会话结束清理会收到拒绝访问；
    # 项目工作区是明确可写且作用域更窄的测试临时根目录。
    configured = getattr(config.option, "basetemp", None)
    configured = configured or os.getenv("QI_AGENT_TEST_TEMP_DIR")
    system_tmp = Path(configured) if configured else (
        Path.cwd() / f".pytest-tmp-{os.getpid()}"
    )
    system_tmp.mkdir(parents=True, exist_ok=True)
    # 交给 pytest 在该父目录下创建/回收唯一 basetemp，避免把当前运行目录
    # 本身当作 basetemp 后触发 Windows cleanup_dead_symlinks 权限异常。
    config.option.basetemp = str(system_tmp)
    temp_dir = str(system_tmp)
    system_tmp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = temp_dir
    os.environ["TMPDIR"] = temp_dir
    os.environ["TEMP"] = temp_dir
    os.environ["TMP"] = temp_dir

    # 测试不得写入真实用户目录；Windows 受控环境下该目录可能被旧进程锁定。
    # 在 pytest 收集业务模块前重定向日志模块的已计算路径。
    # 日志也放在 basetemp 外，避免 pytest 清理临时树时仍被 FileHandler 占用。
    log_dir = Path.cwd() / f".pytest-logs-{os.getpid()}"
    from qi_agent import logging_setup
    from qi_agent.gateway import protocol

    logging_setup._LOG_DIR = str(log_dir)
    protocol._RPC_LOG_DIR = str(log_dir)


@pytest.fixture()
def tmp_path() -> Path:
    """不依赖 pytest tmpdir 插件的 Windows 可写临时目录。"""
    path = Path.cwd() / ".pytest-local" / f"{os.getpid()}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path
