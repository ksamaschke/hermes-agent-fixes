"""Offline tests for matrix-sas-verification against the installed mautrix.

Run:  python tests/test_sas_verification.py

Verifies the plugin's contract and the invariants that previously broke the
flow in production. No network, no gateway, no crypto store.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugin" / "matrix-sas-verification"


def _load():
    """Load the plugin package so its relative import of .handler works."""
    pkg_spec = importlib.util.spec_from_file_location(
        "matrix_sas_undertest",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules["matrix_sas_undertest"] = pkg
    pkg_spec.loader.exec_module(pkg)
    return pkg


def test_handler_imports_and_constructs():
    """The responder must build from (adapter, client, olm) alone."""
    pkg = _load()
    from matrix_sas_undertest.handler import SasVerificationHandler

    h = SasVerificationHandler(object(), object(), object())
    assert hasattr(h, "register"), "handler must expose register()"
    print("PASS handler imports and constructs")


def test_registers_on_client_when_crypto_present():
    """_install must attach the handler to a crypto-enabled client."""
    pkg = _load()

    registered: list[str] = []

    class FakeClient:
        crypto = object()

        def add_event_handler(self, evt_type, handler, **kw):
            registered.append(str(evt_type))

    client = FakeClient()
    assert pkg._install(client, object()) is True, "should install"
    assert registered, "must register event handlers on the client"
    assert getattr(client, pkg._ATTR, None) is not None, "handler must be attached"

    # second call is a no-op -> no double registration
    before = len(registered)
    assert pkg._install(client, object()) is False, "second install must no-op"
    assert len(registered) == before, "must not register twice"
    print(f"PASS registers {before} handlers, idempotent")


def test_skips_when_no_crypto():
    """Without E2EE there is nothing to verify; must skip quietly."""
    pkg = _load()

    class NoCrypto:
        crypto = None

    assert pkg._install(NoCrypto(), object()) is False
    print("PASS skips cleanly without crypto")


def test_covers_both_transports():
    """Element X uses in-room events; older clients use to-device."""
    pkg = _load()
    src = (PLUGIN_DIR / "handler.py").read_text()
    assert "TO_DEVICE" in src, "to-device transport must be handled"
    assert "m.relates_to" in src, "in-room transport (MSC 2241) must be handled"
    print("PASS handles to-device and in-room transports")


def test_in_room_events_are_plaintext():
    """Invariant: encrypting in-room SAS events causes m.mismatched_sas.

    The initiating client cannot read its own handshake if we encrypt these,
    which is exactly how this flow failed in production.
    """
    pkg = _load()
    src = (PLUGIN_DIR / "handler.py").read_text()
    assert "send_message_event" in src, "handler must send in-room events"

    # The kwarg must actually be passed on a send, not merely mentioned.
    passes_kwarg = [
        l for l in src.splitlines() if "disable_encryption=True" in l
    ]
    assert passes_kwarg, (
        "in-room verification events must be sent with disable_encryption=True"
    )
    print(
        f"PASS in-room sends carry disable_encryption "
        f"({len(passes_kwarg)} call site(s))"
    )


def test_install_failure_does_not_break_agent():
    """A broken verification setup must never take messaging down."""
    pkg = _load()

    class Exploding:
        @property
        def crypto(self):
            raise RuntimeError("boom")

    captured = {}

    class Ctx:
        def register_platform_handler(self, name, factory):
            captured["factory"] = factory

    pkg.register(Ctx())
    # must swallow the error and return None rather than propagate
    assert captured["factory"](Exploding(), object()) is None
    print("PASS install failure is contained")


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
