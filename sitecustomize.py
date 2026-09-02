"""解释器启动时的测试环境兜底。

只有在 pytest 场景下生效：把临时目录重定向到仓库内可写位置，避免 Windows
用户临时目录在当前受限环境下不可访问。
（2026-09-02：Codex 会话误删本文件后由 Hermes 按原功能重写恢复。）
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path


def _is_pytest_process() -> bool:
    """按启动参数/环境判断是否 pytest 场景（非 pytest 进程零副作用）。"""
    argv0 = (sys.argv[0] if sys.argv else "").lower()
    if "pytest" in argv0:
        return True
    for index, arg in enumerate(sys.argv):
        if arg == "-m" and index + 1 < len(sys.argv):
            if "pytest" in sys.argv[index + 1].lower():
                return True
    return any(key.startswith("PYTEST_") for key in os.environ)


def _tmp_dir_writable() -> bool:
    try:
        probe = Path(os.environ.get("TMP", Path.cwd().joinpath("tmp").as_posix()))
        probe.mkdir(parents=True, exist_ok=True)
        test_file = probe / f".write-test-{os.getpid()}"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return True
    except OSError:
        return False


def _configure_temp_dir() -> None:
    if not _is_pytest_process():
        return
    if _tmp_dir_writable():
        return
    # 受限环境：重定向到仓库内可写位置（唯一目录防并发冲突）
    candidates = [Path.cwd() / "tmp", Path(os.environ.get("TEMP", ""))] if os.environ.get("TEMP") else [Path.cwd() / "tmp"]
    for base in candidates:
        try:
            target = base / f"qi-{uuid.uuid4().hex[:8]}"
            target.mkdir(parents=True, exist_ok=True)
            os.environ["TMPDIR"] = str(target)
            os.environ["TEMP"] = str(target)
            os.environ["TMP"] = str(target)
            return
        except OSError:
            continue


_configure_temp_dir()
