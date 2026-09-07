"""Offline tests for the identity guard, against the real mautrix OlmMachine."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

import olm
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "matrix-store-guard"))
identity = importlib.import_module("identity")


class _Account:
    def __init__(self, shared: bool):
        self._acc = olm.Account()
        self.shared = shared

    @property
    def identity_keys(self):
        return self._acc.identity_keys


class _Client:
    def __init__(self, mxid, device_id, remote_keys):
        self.mxid, self.device_id = mxid, device_id
        self._remote = remote_keys
        self.queries = 0

    async def query_keys(self, req):
        self.queries += 1
        dk = {}
        if self._remote is not None:
            dk = {self.mxid: {self.device_id: types.SimpleNamespace(keys=self._remote)}}
        return types.SimpleNamespace(device_keys=dk)


class _Machine:
    def __init__(self, account, client):
        self.account, self.client = account, client


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_fresh_device_passes():
    m = _Machine(_Account(shared=False), _Client("@a:x", "DEV", None))
    _run(identity.check_identity(m))


def test_same_identity_passes():
    acc = _Account(shared=False)
    same = identity._local_identity(acc, "DEV")
    m = _Machine(acc, _Client("@a:x", "DEV", same))
    _run(identity.check_identity(m))


def test_different_identity_refused():
    acc = _Account(shared=False)
    other = identity._local_identity(_Account(shared=False), "DEV")
    m = _Machine(acc, _Client("@a:x", "DEV", other))
    with pytest.raises(identity.IdentityMismatch) as ei:
        _run(identity.check_identity(m))
    assert "NEW device id" in str(ei.value)


def test_already_shared_is_not_checked():
    acc = _Account(shared=True)
    other = identity._local_identity(_Account(shared=False), "DEV")
    c = _Client("@a:x", "DEV", other)
    _run(identity.check_identity(_Machine(acc, c)))
    assert c.queries == 0


def test_query_failure_does_not_block():
    class Broken(_Client):
        async def query_keys(self, req):
            raise ConnectionError("down")

    acc = _Account(shared=False)
    _run(identity.check_identity(_Machine(acc, Broken("@a:x", "DEV", None))))


def test_patch_wraps_real_olm_machine():
    from mautrix.crypto.machine import OlmMachine

    before = OlmMachine._share_keys
    assert identity._patch_machine_class() is True
    assert OlmMachine._share_keys is not before
    assert identity._patch_machine_class() is False  # idempotent


def test_guarded_share_keys_refuses_on_mismatch():
    from mautrix.crypto.machine import OlmMachine

    identity._patch_machine_class()
    acc = _Account(shared=False)
    other = identity._local_identity(_Account(shared=False), "DEV")
    m = _Machine(acc, _Client("@a:x", "DEV", other))
    with pytest.raises(identity.IdentityMismatch):
        _run(OlmMachine._share_keys(m, 0))
