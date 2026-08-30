"""Focused tests for the Matrix sync-auth user override bundle."""

from __future__ import annotations

import asyncio as real_asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from mautrix.errors import MUnknownToken, MatrixConnectionError

OVERRIDE_ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "override"
    / "platforms"
    / "matrix"
    / "adapter.py"
)


def _load_adapter_module():
    spec = importlib.util.spec_from_file_location(
        "matrix_user_override_under_test", OVERRIDE_ADAPTER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _asyncio_proxy(no_delay):
    return types.SimpleNamespace(
        wait_for=real_asyncio.wait_for,
        sleep=no_delay,
        CancelledError=real_asyncio.CancelledError,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_type", "message"),
    [
        (MatrixConnectionError, "500 Internal Server Error (request id req-4012ab)"),
        (MatrixConnectionError, "connection timeout to peer ws-403-prod"),
        (RuntimeError, "upstream unauthorized-route temporarily unavailable"),
        (RuntimeError, "forbidden-zone proxy connection reset"),
    ],
)
async def test_free_form_auth_like_text_remains_retryable(
    monkeypatch, exception_type, message
):
    adapter_module = _load_adapter_module()

    class SyncStore:
        async def get_next_batch(self):
            return None

    class Client:
        def __init__(self):
            self.sync_store = SyncStore()
            self.attempts = 0

        async def sync(self, **_kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise exception_type(message)
            raise real_asyncio.CancelledError

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(adapter_module, "asyncio", _asyncio_proxy(no_delay))

    client = Client()
    instance = object.__new__(adapter_module.MatrixAdapter)
    instance._client = client
    instance._closing = False

    await instance._sync_loop()

    assert client.attempts == 2


@pytest.mark.asyncio
async def test_typed_unknown_token_stops_without_retry(monkeypatch):
    adapter_module = _load_adapter_module()

    class SyncStore:
        async def get_next_batch(self):
            return None

    class Client:
        def __init__(self):
            self.sync_store = SyncStore()
            self.attempts = 0

        async def sync(self, **_kwargs):
            self.attempts += 1
            raise MUnknownToken(401, "access token is no longer valid")

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(adapter_module, "asyncio", _asyncio_proxy(no_delay))

    client = Client()
    instance = object.__new__(adapter_module.MatrixAdapter)
    instance._client = client
    instance._closing = False

    await instance._sync_loop()

    assert client.attempts == 1


@pytest.mark.asyncio
async def test_structured_unknown_token_response_stops_without_retry(monkeypatch):
    adapter_module = _load_adapter_module()

    class SyncStore:
        async def get_next_batch(self):
            return None

    class Client:
        def __init__(self):
            self.sync_store = SyncStore()
            self.attempts = 0

        async def sync(self, **_kwargs):
            self.attempts += 1
            return types.SimpleNamespace(
                errcode="M_UNKNOWN_TOKEN",
                message="The access token is no longer valid",
            )

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(adapter_module, "asyncio", _asyncio_proxy(no_delay))

    client = Client()
    instance = object.__new__(adapter_module.MatrixAdapter)
    instance._client = client
    instance._closing = False

    await instance._sync_loop()

    assert client.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("errcode", [None, "M_LIMIT_EXCEEDED"])
async def test_unknown_token_response_message_without_matching_errcode_retries(
    monkeypatch, errcode
):
    adapter_module = _load_adapter_module()
    delays = []

    class SyncStore:
        async def get_next_batch(self):
            return None

    class Client:
        def __init__(self):
            self.sync_store = SyncStore()
            self.attempts = 0

        async def sync(self, **_kwargs):
            self.attempts += 1
            if self.attempts == 1:
                return types.SimpleNamespace(
                    errcode=errcode,
                    message="transient proxy unknown_token route",
                )
            raise real_asyncio.CancelledError

    async def record_delay(seconds):
        delays.append(seconds)

    monkeypatch.setattr(adapter_module, "asyncio", _asyncio_proxy(record_delay))

    client = Client()
    instance = object.__new__(adapter_module.MatrixAdapter)
    instance._client = client
    instance._closing = False

    await instance._sync_loop()

    assert client.attempts == 2
    assert delays == [5]


@pytest.mark.asyncio
async def test_cancelled_sync_exits_without_retry_or_delay(monkeypatch):
    adapter_module = _load_adapter_module()
    delays = []

    class SyncStore:
        async def get_next_batch(self):
            return None

    class Client:
        def __init__(self):
            self.sync_store = SyncStore()
            self.attempts = 0

        async def sync(self, **_kwargs):
            self.attempts += 1
            raise real_asyncio.CancelledError

    async def record_delay(seconds):
        delays.append(seconds)

    monkeypatch.setattr(adapter_module, "asyncio", _asyncio_proxy(record_delay))

    client = Client()
    instance = object.__new__(adapter_module.MatrixAdapter)
    instance._client = client
    instance._closing = False

    await instance._sync_loop()

    assert client.attempts == 1
    assert delays == []


def test_registration_captures_override_factory_and_restores_upstream():
    adapter_module = _load_adapter_module()
    upstream_factory = adapter_module.upstream._build_adapter
    captured = {}

    class Context:
        def register_platform(self, **kwargs):
            captured.update(kwargs)

    adapter_module.register(Context())

    assert captured["name"] == "matrix"
    assert captured["adapter_factory"] is adapter_module._build_adapter
    assert adapter_module.upstream._build_adapter is upstream_factory


def test_registration_restores_upstream_factory_after_failure(monkeypatch):
    adapter_module = _load_adapter_module()
    upstream_factory = adapter_module.upstream._build_adapter

    def fail_registration(_ctx):
        raise RuntimeError("registration failed")

    monkeypatch.setattr(adapter_module.upstream, "register", fail_registration)

    with pytest.raises(RuntimeError, match="registration failed"):
        adapter_module.register(object())

    assert adapter_module.upstream._build_adapter is upstream_factory


def _run_hermes_cli(home: Path, *args: str) -> str:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", *args],
        check=True,
        capture_output=True,
        cwd=Path.cwd(),
        env=env,
        text=True,
    )
    return result.stdout


def _matrix_plugin_row(home: Path) -> dict:
    rows = json.loads(_run_hermes_cli(home, "plugins", "list", "--json"))
    return next(row for row in rows if row["name"] == "matrix-platform")


def test_documented_user_plugin_install_and_rollback(tmp_path):
    home = tmp_path / "profile"
    user_plugin = home / "plugins" / "platforms" / "matrix"
    backup_plugin = home / "plugin-backups" / "matrix-sync-auth-type-aware-v2"
    bundle_plugin = OVERRIDE_ADAPTER.parent

    user_plugin.parent.mkdir(parents=True)
    shutil.copytree(bundle_plugin, user_plugin)

    _run_hermes_cli(
        home,
        "plugins",
        "enable",
        "platforms/matrix",
        "--no-allow-tool-override",
    )
    assert _matrix_plugin_row(home)["source"] == "user"
    assert _matrix_plugin_row(home)["status"] == "enabled"

    backup_plugin.parent.mkdir(parents=True)
    shutil.move(user_plugin, backup_plugin)

    assert _matrix_plugin_row(home)["source"] == "bundled"
    assert _matrix_plugin_row(home)["status"] == "enabled"
