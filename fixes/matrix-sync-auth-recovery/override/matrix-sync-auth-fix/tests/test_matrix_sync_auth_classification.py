"""Regression: Matrix sync-loop error classification.

A network timeout must never be misclassified as a permanent auth failure.

aiohttp's timeout errors embed the full request URL in their message, and
mautrix rewraps any ``ClientError`` as ``MatrixConnectionError``. The sync URL
carries ``?since=<next_batch>``; a synthetic cursor can therefore contain the
substring ``401``/``403`` without representing an HTTP or Matrix auth error.

mautrix/aiohttp are imported lazily inside the tests: importing them at module
scope makes this file's collection order-dependent, because other gateway tests
transiently place plugin directories on ``sys.path`` (see the anti-pattern
guard in tests/gateway/conftest.py).
"""

import ast
import asyncio
import importlib
import importlib.util
import pathlib
import sys
from types import SimpleNamespace

import pytest


SYNC = "https://matrix.example.test/_matrix/client/v3/sync"

# The classification now lives in the user plugin that overrides the bundled
# Matrix adapter, NOT in Hermes core. Core is deliberately left stock (it still
# contains the buggy substring test); these tests pin the PLUGIN's behaviour.
ADAPTER = pathlib.Path(__file__).resolve().parents[1] / "__init__.py"


@pytest.fixture(autouse=True)
def _real_mautrix():
    """Guarantee the genuine mautrix SDK, not a leaked stub.

    ``test_matrix_approval_reaction_fail_closed.py`` injects empty
    ``mautrix.*`` placeholder modules via ``sys.modules.setdefault`` and never
    removes them, so any test collected after it sees an empty
    ``mautrix.errors``. Detect that, reload the real package for the duration
    of this module, then restore ``sys.modules`` exactly as found so the
    stub-dependent tests keep working.
    """
    saved = {k: v for k, v in sys.modules.items() if k.startswith("mautrix")}
    try:
        errors = importlib.import_module("mautrix.errors")
    except ImportError:
        errors = None
    if errors is None or not hasattr(errors, "MatrixConnectionError"):
        for key in list(sys.modules):
            if key.startswith("mautrix"):
                del sys.modules[key]
        importlib.invalidate_caches()
    try:
        yield
    finally:
        for key in list(sys.modules):
            if key.startswith("mautrix"):
                del sys.modules[key]
        sys.modules.update(saved)


def classify(exc):
    """Call the shipped adapter classifier used by the plugin."""
    return _load_local_plugin()._is_permanent_auth_error(exc)


def _timeout_with_token(token):
    """A real aiohttp timeout whose message embeds the sync URL."""
    from aiohttp.client_exceptions import ConnectionTimeoutError
    from mautrix.errors import MatrixConnectionError

    url = f"{SYNC}?since={token}&timeout=30000"
    return MatrixConnectionError(
        str(ConnectionTimeoutError(f"Connection timeout to host {url}"))
    )


def _request_error(status, errcode):
    """Build a real homeserver error via mautrix's own factory."""
    from mautrix.errors.request import make_request_error

    return make_request_error(
        http_status=status,
        text="{}",
        errcode=errcode,
        message="denied",
    )


def _load_local_plugin():
    spec = importlib.util.spec_from_file_location(
        "matrix_sync_auth_fix_v2_test", ADAPTER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SyncClient:
    def __init__(self, responses):
        self.sync_store = self
        self.responses = list(responses)
        self.calls = 0

    async def get_next_batch(self):
        return "initial-token"

    async def put_next_batch(self, _next_batch):
        pass

    async def sync(self, **_kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


async def _run_non_dict_response(response, monkeypatch):
    module = _load_local_plugin()

    class BaseAdapter:
        pass

    adapter_cls = module._build_patched_class(BaseAdapter)
    adapter = adapter_cls.__new__(adapter_cls)
    adapter._client = _SyncClient([response, asyncio.CancelledError()])
    adapter._closing = False

    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    await adapter._sync_loop()
    return adapter._client.calls, delays


# Synthetic Synapse-shaped cursors with auth-like digits in different fields.
POISONED_TOKENS = [
    "s1000_2000_3000_4000_5000_6_7403_8_0_1_2_1_1_1",
    "s1000_2000_3000_7401_5000_6_7000_8_0_1_2_1_1_1",
    "s1000_2000_3000_4000_5403_6_7000_8_0_1_2_1_1_1",
    "s23401_2000_3000_4000_5000_6_7000_8_0_1_2_1_1_1",
    "s1000_2000_3403_4000_5000_6_7000_8_0_1_2_1_1_1",
    "s1000_2000_3000_4013_5000_6_7000_8_0_1_2_1_1_1",
]

CLEAN_TOKENS = [
    "s1000_2000_3000_4000_5000_6_7000_8_0_1_2_1_1_1",
    "s1100_2200_3300_4400_5500_6_7700_8_0_1_2_1_1_1",
]


@pytest.mark.parametrize("token", POISONED_TOKENS)
def test_timeout_with_poisoned_token_is_retried(token):
    """The exact bug: a timeout must not be read as an auth failure."""
    assert classify(_timeout_with_token(token)) is False, (
        f"network timeout misclassified as permanent auth failure "
        f"because next_batch {token!r} contains '401'/'403'"
    )


@pytest.mark.parametrize("token", CLEAN_TOKENS)
def test_timeout_with_clean_token_is_retried(token):
    assert classify(_timeout_with_token(token)) is False


def test_server_timeout_is_retried():
    from aiohttp.client_exceptions import ServerTimeoutError
    from mautrix.errors import MatrixConnectionError

    exc = MatrixConnectionError(
        str(ServerTimeoutError(f"Timeout on reading data from {SYNC}?since=s403_1"))
    )
    assert classify(exc) is False


def test_connector_error_is_retried():
    from aiohttp.client_exceptions import ClientConnectorError
    from mautrix.errors import MatrixConnectionError

    class _Key:
        host, port, ssl, is_ssl = "matrix.example.test", 403, True, True

        def __repr__(self):
            return f"ConnectionKey(host={self.host!r}, port={self.port})"

    exc = MatrixConnectionError(
        str(ClientConnectorError(_Key(), OSError(60, "Operation timed out")))
    )
    assert classify(exc) is False


def test_bare_connection_error_is_retried():
    """MatrixConnectionError is a transport failure by definition."""
    from mautrix.errors import MatrixConnectionError

    assert classify(MatrixConnectionError("Cannot connect to host")) is False


@pytest.mark.asyncio
async def test_response_message_unknown_token_without_errcode_is_retried(monkeypatch):
    response = SimpleNamespace(
        message="transient proxy unknown_token route",
        errcode=None,
    )
    calls, delays = await _run_non_dict_response(response, monkeypatch)
    assert calls == 2
    assert delays == [5]


@pytest.mark.asyncio
async def test_response_message_unknown_token_with_other_errcode_is_retried(
    monkeypatch,
):
    response = SimpleNamespace(
        message="transient proxy unknown_token route",
        errcode="M_LIMIT_EXCEEDED",
    )
    calls, delays = await _run_non_dict_response(response, monkeypatch)
    assert calls == 2
    assert delays == [5]


def test_http_401_without_structured_unknown_token_is_retryable():
    class GenericHttpError(Exception):
        http_status = 401

    assert _load_local_plugin()._is_permanent_auth_error(GenericHttpError("denied")) is False


def test_matrix_request_unknown_token_is_terminal():
    assert _load_local_plugin()._is_permanent_auth_error(
        _request_error(401, "M_UNKNOWN_TOKEN")
    ) is True


def test_typed_m_unknown_token_is_terminal():
    from mautrix.errors import MUnknownToken

    assert _load_local_plugin()._is_permanent_auth_error(
        MUnknownToken(401, "denied")
    ) is True


def test_response_classifier_requires_structured_unknown_token_source():
    source = ADAPTER.read_text(encoding="utf-8")
    assert 'sync_errcode == "M_UNKNOWN_TOKEN"' in source
    assert "sync_message" not in source
    assert '"unknown_token" in' not in source.lower()


# --- genuine auth failures MUST still stop the loop ------------------------


@pytest.mark.parametrize(
    "status,errcode,expected",
    [
        (401, "M_UNKNOWN_TOKEN", True),
        (401, "M_MISSING_TOKEN", False),
        (403, "M_FORBIDDEN", False),
    ],
)
def test_real_auth_failure_still_stops_sync(status, errcode, expected):
    assert classify(_request_error(status, errcode)) is expected


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
def test_server_side_http_errors_are_retried(status):
    assert classify(_request_error(status, "M_UNKNOWN")) is False


def test_old_classifier_would_have_failed():
    """Pin the defect: the previous text-matching logic breaks on these."""

    def old_classify(exc):
        s = str(exc).lower()
        return "401" in s or "403" in s or "unauthorized" in s or "forbidden" in s

    poisoned = _timeout_with_token(POISONED_TOKENS[0])
    assert old_classify(poisoned) is True, "expected the old bug to reproduce"
    assert classify(poisoned) is False, "new classifier must not reproduce it"


# --- the mirror must match the shipped adapter ----------------------------


def _sync_loop_classifier_source():
    """Extract the live auth-classification branch from _sync_loop."""
    src = ADAPTER.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_sync_loop":
            for handler in ast.walk(node):
                if not isinstance(handler, ast.ExceptHandler):
                    continue
                for sub in handler.body:
                    for inner in ast.walk(sub):
                        if isinstance(inner, ast.If):
                            seg = ast.get_source_segment(src, inner) or ""
                            if "permanent auth error" in seg:
                                return seg
    return None


def test_adapter_classifier_matches_this_mirror():
    """Guard against the mirror drifting from the shipped adapter."""
    seg = _sync_loop_classifier_source()
    assert seg, "auth-classification branch not found in _sync_loop"
    assert "http_status" in seg, "adapter no longer keys on http_status"
    assert "errcode" in seg, "adapter no longer keys on errcode"
    for token in ('"401" in', '"403" in', "in err_str"):
        assert token not in seg, (
            f"adapter regressed to message-text classification ({token!r}); "
            "a network timeout will kill the sync loop again"
        )


# --- the combined user-plugin registration contract ----------------------


def _load_plugin_registration():
    import inspect

    spec = importlib.util.spec_from_file_location("matrix_sync_auth_fix_test", ADAPTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Context:
        registration = None

        def register_platform(self, **kwargs):
            self.registration = kwargs

    ctx = Context()
    module.register(ctx)
    assert ctx.registration is not None
    nonlocals = inspect.getclosurevars(
        ctx.registration["adapter_factory"]
    ).nonlocals
    nonlocals.update(
        inspect.getclosurevars(nonlocals["_patched_class"]).nonlocals
    )
    return module, ctx.registration, nonlocals


def test_resolved_matrix_base_is_the_sibling_patched_adapter():
    _plugin, registration, nonlocals = _load_plugin_registration()
    sibling = nonlocals["_patched_upstream"]()
    sibling_path = pathlib.Path(sibling.__file__).resolve()

    assert sibling_path == ADAPTER.with_name("patched_upstream.py").resolve()
    assert sibling_path.name == "patched_upstream.py"
    assert registration["adapter_factory"] is not None


def test_patched_base_has_e2ee_guards_and_fail_closed_zero_recipient_path():
    _plugin, _registration, nonlocals = _load_plugin_registration()
    sibling = nonlocals["_patched_upstream"]()
    source = pathlib.Path(sibling.__file__).read_text(encoding="utf-8")

    assert callable(sibling._build_hermes_olm_machine)
    assert hasattr(sibling.MatrixAdapter, "_ensure_encrypted_room_ready")
    assert sibling.HERMES_E2EE_RECIPIENT_ENFORCEMENT_MARKER == (
        "matrix-e2ee-recipient-enforced-v1"
    )
    assert "if not targets:" in source
    assert "remove_outbound_group_sessions" in source
    assert "No encrypted to-device recipients" in source


def test_constructed_adapter_class_uses_sibling_and_local_sync_override():
    _plugin, _registration, nonlocals = _load_plugin_registration()
    sibling = nonlocals["_patched_upstream"]()
    adapter_cls = nonlocals["_patched_class"]()

    assert issubclass(adapter_cls, sibling.MatrixAdapter)
    assert adapter_cls.__mro__[1] is sibling.MatrixAdapter
    assert "_sync_loop" in adapter_cls.__dict__
    assert adapter_cls._sync_loop.__qualname__.startswith("_build_patched_class")


def test_missing_recipient_marker_fails_closed_before_adapter_construction():
    _plugin, _registration, nonlocals = _load_plugin_registration()
    sibling = nonlocals["_patched_upstream"]()
    marker = sibling.HERMES_E2EE_RECIPIENT_ENFORCEMENT_MARKER
    del sibling.HERMES_E2EE_RECIPIENT_ENFORCEMENT_MARKER
    try:
        with pytest.raises(RuntimeError, match="failed closed"):
            nonlocals["_patched_class"]()
    finally:
        sibling.HERMES_E2EE_RECIPIENT_ENFORCEMENT_MARKER = marker


def test_max_message_length_drift_fails_closed_before_adapter_construction():
    _plugin, _registration, nonlocals = _load_plugin_registration()
    sibling = nonlocals["_patched_upstream"]()
    original = sibling.DEFAULT_MAX_MESSAGE_LENGTH
    sibling.DEFAULT_MAX_MESSAGE_LENGTH = original + 1
    try:
        with pytest.raises(RuntimeError, match="max message length"):
            nonlocals["_patched_class"]()
    finally:
        sibling.DEFAULT_MAX_MESSAGE_LENGTH = original


def test_single_plugin_registration_needs_no_duplicate_matrix_directory():
    _plugin, registration, _nonlocals = _load_plugin_registration()

    assert registration["name"] == "matrix"
    assert not (ADAPTER.parent.parent / "platforms" / "matrix").exists()
