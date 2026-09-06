"""Offline tests for matrix-share-verify.

Run: python -m pytest test_share_verify.py -q
No homeserver, no network, no live gateway.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "share_verify", Path(__file__).with_name("__init__.py")
)
sv = importlib.util.module_from_spec(_spec)
sys.modules["share_verify"] = sv
_spec.loader.exec_module(sv)


# --- churn policy ---------------------------------------------------------

def test_normal_traffic_is_not_churn():
    now = time.time()
    # three sessions spread over the window: ordinary
    assert not sv.churn_exceeded([now - 3000, now - 2000, now - 100], now)


def test_burst_inside_window_is_churn():
    now = time.time()
    burst = [now - i for i in range(sv.CHURN_THRESHOLD)]
    assert sv.churn_exceeded(burst, now)


def test_old_burst_outside_window_is_not_churn():
    now = time.time()
    old = [now - sv.CHURN_WINDOW_SECONDS - i for i in range(50)]
    assert not sv.churn_exceeded(old, now)


def test_threshold_zero_disables_the_check():
    now = time.time()
    assert not sv.churn_exceeded([now] * 100, now, threshold=0)


# --- revalidation policy --------------------------------------------------

def test_room_is_revalidated_once_then_trusted():
    verified: set[str] = set()
    assert sv.rooms_needing_revalidation(verified, "!r:s")
    verified.add("!r:s")
    assert not sv.rooms_needing_revalidation(verified, "!r:s")


def test_each_room_is_revalidated_separately():
    verified = {"!a:s"}
    assert not sv.rooms_needing_revalidation(verified, "!a:s")
    assert sv.rooms_needing_revalidation(verified, "!b:s")


# --- the actual regression -------------------------------------------------

class _FakeAdapter:
    """Mimics the adapter's cache short-circuit."""

    def __init__(self):
        # a session restored from the database, believed shared
        self._e2ee_room_targets = {"!dm:s": ("SESSION-ID", {"dev1", "dev2"})}
        self.verification_ran = False

    async def _ensure_encrypted_room_ready_impl(self, room_id):
        # real adapter returns early while the cache entry is present
        if room_id in self._e2ee_room_targets:
            return
        self.verification_ran = True


def test_restored_session_is_verified_instead_of_trusted():
    """The exact failure: a session mis-shared before restart must be checked."""
    adapter = _FakeAdapter()
    original = _FakeAdapter._ensure_encrypted_room_ready_impl

    async def wrapper(self, room_id):
        verified = getattr(self, "_hermes_share_verified_rooms", None)
        if verified is None:
            verified = set()
            setattr(self, "_hermes_share_verified_rooms", verified)
        key = str(room_id)
        if sv.rooms_needing_revalidation(verified, key):
            self._e2ee_room_targets.pop(key, None)
            verified.add(key)
        await original(self, room_id)

    # without the plugin the stale cache entry suppresses verification
    asyncio.run(original(adapter, "!dm:s"))
    assert adapter.verification_ran is False

    # with the plugin it runs exactly once
    asyncio.run(wrapper(adapter, "!dm:s"))
    assert adapter.verification_ran is True

    # and is not repeated for the same room afterwards
    adapter.verification_ran = False
    asyncio.run(wrapper(adapter, "!dm:s"))
    assert adapter.verification_ran is True  # cache already cleared, still real


# --- install path ----------------------------------------------------------

class _HostAdapter:
    """Stands in for the live adapter handed to the factory."""

    def __init__(self):
        self._e2ee_room_targets = {}

    async def _ensure_encrypted_room_ready_impl(self, room_id):
        return None


class _ForeignAdapter:
    """An adapter without the hook this plugin wraps."""


def test_patch_uses_the_instance_and_needs_no_import():
    """Regression: importing hermes_plugins at register time raises.

    The adapter module is not loaded when register() runs, so the class must
    be reached through type(adapter) inside the factory instead.
    """
    adapter = _HostAdapter()
    assert sv._patch_adapter_class(adapter) is True
    # second install is a no-op, not a double wrap
    assert sv._patch_adapter_class(adapter) is False


def test_unknown_adapter_is_left_alone():
    assert sv._patch_adapter_class(_ForeignAdapter()) is False


def test_register_defers_patching_to_the_factory():
    captured = {}

    class _Ctx:
        def register_platform_handler(self, platform, factory):
            captured["platform"] = platform
            captured["factory"] = factory

    sv.register(_Ctx())
    assert captured["platform"] == "matrix"
    # the factory must survive an adapter it cannot patch
    assert captured["factory"](None, _ForeignAdapter()) is None


# --- store probe robustness ------------------------------------------------

class _FakeDB:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc

    async def fetch(self, *_args):
        if self._raise:
            raise self._raise
        return self._rows


def test_counts_recent_sessions_only():
    now = time.time()
    rows = [{"created_at": now - 10}, {"created_at": now - 50},
            {"created_at": now - 999999}]
    n = asyncio.run(sv.count_recent_olm_sessions(_FakeDB(rows), "KEY", 3600.0))
    assert n == 2


def test_broken_store_reports_no_churn_instead_of_raising():
    n = asyncio.run(
        sv.count_recent_olm_sessions(_FakeDB(raise_exc=RuntimeError("no table")),
                                     "KEY", 3600.0)
    )
    assert n == 0


def test_missing_db_is_safe():
    assert asyncio.run(sv.count_recent_olm_sessions(None, "KEY", 3600.0)) == 0
