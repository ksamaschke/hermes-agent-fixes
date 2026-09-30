"""matrix-runtime-py314 - expose a user-owned Matrix E2EE dependency directory.

WHY THIS EXISTS
---------------
Hermes runs from a managed Python runtime that it rebuilds on update. Its
`matrix` extra is gated to Linux (`[tool.hermes.extras-platforms]`), because
`python-olm` ships no macOS wheel and its bundled libolm fails to compile with
current Apple clang and CMake >= 4. On macOS the managed runtime therefore has
no `mautrix`/`olm`, and the Matrix adapter reports "requirements not met".

WHAT IT DOES
------------
Appends `$HERMES_HOME/vendor/matrix-py314` (override: MATRIX_PY314_VENDOR_DIR)
to `sys.path` when the running interpreter is CPython 3.14. The directory holds
a locally built cp314 python-olm wheel plus mautrix, asyncpg, aiosqlite,
aiohttp-socks, python-socks, cffi, base58, pycryptodome and unpaddedbase64. It is
APPENDED, so anything the managed environment already provides (aiohttp, yarl,
attrs, ...) wins and this plugin can never shadow an upstream package.

It never installs anything, never touches Hermes code and does nothing on other
Python versions or when the directory is absent. The compiled olm library only
loads for the interpreter it was built for, so a Python minor upgrade needs a
rebuild (see fixes/matrix-runtime-py314/README.md in hermes-agent-fixes).

Plugins load in name order and this one sorts before `matrix-store-guard`,
which imports mautrix at registration.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_REQUIRED_PYTHON = (3, 14)


def _vendor_dir() -> Path:
    override = os.environ.get("MATRIX_PY314_VENDOR_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    home = os.environ.get("HERMES_HOME", "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".hermes"
    # Profiles live in <root>/profiles/<name>; the vendor dir sits at the root.
    if base.parent.name == "profiles":
        base = base.parent.parent
    return base / "vendor" / "matrix-py314"


def install() -> bool:
    """Append the vendor dir to sys.path. Returns True when it was added."""
    if sys.version_info[:2] != _REQUIRED_PYTHON:
        return False
    vendor = _vendor_dir()
    if not vendor.is_dir():
        log.debug("matrix-runtime-py314: %s not found, skipping", vendor)
        return False
    entry = str(vendor)
    if entry in sys.path:
        return False
    sys.path.append(entry)
    import importlib

    importlib.invalidate_caches()
    log.info("matrix-runtime-py314: Matrix E2EE dependencies exposed from %s", entry)
    return True


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""
    try:
        install()
    except Exception:  # never block startup
        log.exception("matrix-runtime-py314: failed to expose vendor directory")
