"""Test environment bootstrap."""

from __future__ import annotations

import os
from pathlib import Path


def _set_test_home() -> None:
    temp_root = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp")))
    home = temp_root / "qi-agent-home"
    pytest_root = temp_root / "qi-agent-pytest"
    home.mkdir(parents=True, exist_ok=True)
    pytest_root.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    os.environ["HOMEDRIVE"] = ""
    os.environ["HOMEPATH"] = str(home)
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)
    os.environ["TMPDIR"] = str(temp_root)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(pytest_root)


_set_test_home()
