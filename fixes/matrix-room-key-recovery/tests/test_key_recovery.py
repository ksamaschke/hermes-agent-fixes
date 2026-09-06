"""Offline tests for matrix-key-recovery against the installed mautrix.

Run:  python -m pytest tests/test_key_recovery.py -q
      (or: python tests/test_key_recovery.py)

No network, no live gateway, no crypto store. Verifies the plugin's contract
against the real mautrix classes it subclasses.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "matrix-key-recovery" / "__init__.py"


def _load():
    spec = importlib.util.spec_from_file_location("matrix_key_recovery_undertest", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_subclasses_stock_dispatcher():
    """The recovering dispatcher must BE a DecryptionDispatcher."""
    from mautrix.client.encryption_manager import DecryptionDispatcher

    mod = _load()
    cls = mod._build_dispatcher()
    assert issubclass(cls, DecryptionDispatcher), "must subclass the stock dispatcher"
    print("PASS subclasses stock DecryptionDispatcher")


def test_idempotent_per_session():
    """A burst from one session yields exactly one key request."""
    mod = _load()
    cls = mod._build_dispatcher()
    d = cls.__new__(cls)
    d._requested = set()

    room, sess = "!room:example.org", "SESSIONID"
    first = d._first_time(room, sess)
    repeats = [d._first_time(room, sess) for _ in range(5)]

    assert first is True, "first sighting must request"
    assert not any(repeats), "repeats must not request again"
    assert d._first_time(room, "OTHER") is True, "a different session must request"
    print("PASS one request per (room, session)")


def test_requested_set_is_bounded():
    """The dedupe set must not grow without bound in a long-lived process."""
    mod = _load()
    cls = mod._build_dispatcher()
    d = cls.__new__(cls)
    d._requested = set()

    for i in range(mod._REQUESTED_MAX + 100):
        d._first_time("!r:example.org", f"s{i}")

    assert len(d._requested) <= mod._REQUESTED_MAX, "dedupe set must stay bounded"
    print(f"PASS dedupe set bounded at {mod._REQUESTED_MAX}")


def test_never_decrypts_to_device_events():
    """Safety invariant: the plugin must not consume olm to-device messages.

    An olm message decrypts exactly once. Decrypting it here would destroy the
    m.room_key payloads inside and break every other conversation.
    """
    src = PLUGIN.read_text()
    forbidden = ("decrypt_olm_event", "handle_to_device", "decrypt_to_device")
    hits = [
        f
        for f in forbidden
        if any(
            f in line and not line.lstrip().startswith("#")
            for line in src.splitlines()
        )
    ]
    assert not hits, f"plugin must not touch to-device decryption: {hits}"
    print("PASS does not decrypt to-device events")


def test_wait_is_bounded():
    """No answer must not hang the dispatcher forever."""
    mod = _load()
    assert isinstance(mod.KEY_WAIT_SECONDS, (int, float))
    assert 0 < mod.KEY_WAIT_SECONDS <= 60, "wait must be bounded and sane"
    print(f"PASS bounded wait ({mod.KEY_WAIT_SECONDS}s)")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {exc}")
    print("\nall passed" if not fails else f"\n{fails} failed")
    sys.exit(1 if fails else 0)
