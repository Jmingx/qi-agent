"""pytest 环境兜底。

主要用途是给 pytest 分配一个每次运行都唯一的 basetemp，避免系统临时目录
和旧的残留目录把 `tmp_path` fixture 搞坏。
（2026-09-02：Codex 会话误删本文件后由 Hermes 按原功能重写恢复。）
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    # 不把 pytest 临时目录放进仓库根下的 tmp/，否则 Windows ACL / 残留
    # symlink 会让 cleanup_dead_symlinks 直接炸掉。
    system_tmp = Path(tempfile.gettempdir()) / "qi-agent-pytest"
    system_tmp.mkdir(parents=True, exist_ok=True)
    basetemp = system_tmp / f"basetemp-{os.getpid()}"
    basetemp.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(basetemp)
    temp_dir = str(basetemp)
    os.environ["TMPDIR"] = temp_dir
    os.environ["TEMP"] = temp_dir
    os.environ["TMP"] = temp_dir
