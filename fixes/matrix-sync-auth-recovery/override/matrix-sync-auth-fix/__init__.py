"""Matrix sync auth classification fix — user plugin override.

Hermes core ships a Matrix adapter whose sync loop decides that an error is a
*permanent* authentication failure by lowercasing the exception message and
searching it for "401", "403", "unauthorized" or "forbidden".  Any hit stops
the sync loop for good.

That test false-positives on ordinary transport faults because aiohttp embeds
the full request URL in timeout messages, mautrix rewraps ``ClientError``, and
the sync URL carries a Synapse ``next_batch`` cursor. A cursor can contain the
substring "401" or "403" without representing an auth failure.

This plugin subclasses its sibling-pinned, recipient-enforcing adapter and
overrides ``_sync_loop`` with a version that classifies only typed Matrix
``M_UNKNOWN_TOKEN`` failures. Core is left byte-for-byte stock.

Maintenance note: ``_sync_loop`` is copied from the bundled adapter because
core exposes no narrower seam (there is no ``_is_permanent_auth_error()`` hook
to override).  ``verify_parity()`` below re-reads the bundled source at import
time and warns if the upstream method changed, so this override cannot rot
silently.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

try:
    from mautrix.errors import MUnknownToken, MatrixRequestError
except ImportError:  # pragma: no cover - dependency check happens on use
    MUnknownToken = MatrixRequestError = None

logger = logging.getLogger(__name__)

# Synthetic module name used to load the sibling-pinned Matrix adapter lazily.
PATCHED_MODULE_NAME = f"{__name__}.patched_upstream"
E2EE_SYNC_MARKER = (
    "matrix-e2ee-recipient-enforced-v1+matrix-sync-auth-type-aware-v2"
)

# Fingerprint of the bundled _sync_loop body this override was derived from.
# Only the *non-classification* logic matters for parity: if core changes how
# it syncs, dispatches, or advances the token, this override is stale.
_PARITY_MARKERS = (
    "next_batch = await client.sync_store.get_next_batch()",
    "timeout=45.0,",
    "m_unknown_token",
    "await self._dispatch_sync(sync_data)",
    "self._schedule_pending_invite_joins(sync_data)",
    "await client.sync_store.put_next_batch(nb)",
)

# The buggy classification we are replacing. If this is ABSENT from the
# bundled adapter, core has been fixed (or already patched) and this plugin
# is redundant.
_BUGGY_MARKER = 'err_str = str(exc).lower()'


def _bundled_sync_loop_source() -> Optional[str]:
    """Return the bundled adapter's ``_sync_loop`` source, or None.

    Reads the file from disk rather than importing, so parity can be checked
    before the bundled module has been lazily loaded.
    """
    try:
        import ast
        from pathlib import Path

        from hermes_cli.plugins import get_bundled_plugins_dir

        path = (
            Path(get_bundled_plugins_dir()) / "platforms" / "matrix" / "adapter.py"
        )
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                and node.name == "_sync_loop"
            ):
                return ast.get_source_segment(text, node)
        return None
    except Exception as exc:  # pragma: no cover - diagnostic only
        logger.debug("matrix-sync-auth-fix: cannot read bundled source: %s", exc)
        return None


def verify_parity() -> dict:
    """Compare this override against the bundled ``_sync_loop``.

    Returns a dict describing drift.  Logged at import; also callable from a
    health check.
    """
    src = _bundled_sync_loop_source()
    if src is None:
        return {"status": "unknown", "reason": "bundled source unavailable"}

    missing = [m for m in _PARITY_MARKERS if m not in src]
    core_still_buggy = _BUGGY_MARKER in src

    if missing:
        logger.warning(
            "matrix-sync-auth-fix: bundled _sync_loop CHANGED upstream "
            "(missing markers: %s). Re-derive this override before trusting it.",
            ", ".join(missing),
        )
        return {"status": "drift", "missing": missing,
                "core_still_buggy": core_still_buggy}

    if not core_still_buggy:
        logger.info(
            "matrix-sync-auth-fix: bundled adapter no longer contains the "
            "substring classification — this override may be redundant."
        )
        return {"status": "core_fixed", "missing": [],
                "core_still_buggy": False}

    return {"status": "ok", "missing": [], "core_still_buggy": True}


def _is_permanent_auth_error(exc: BaseException) -> bool:
    """Return true only for typed or structured ``M_UNKNOWN_TOKEN`` errors."""
    if MUnknownToken is not None and isinstance(exc, MUnknownToken):
        return True
    if MatrixRequestError is not None and isinstance(exc, MatrixRequestError):
        errcode = str(getattr(exc, "errcode", "") or "").strip().upper()
        return errcode == "M_UNKNOWN_TOKEN"
    return False


def _build_patched_class(base_adapter_cls):
    """Return a sibling-adapter subclass with corrected sync classification."""
    _MatrixBaseAdapter = base_adapter_cls

    class PatchedMatrixAdapter(_MatrixBaseAdapter):  # type: ignore[misc,valid-type]
        """Pinned Matrix adapter with transport-safe auth classification."""

        async def _sync_loop(self) -> None:
            """Continuously sync with the homeserver.

            Derived from the pinned upstream implementation; the behavioural
            changes are structured response classification and retry handling
            plus typed exception classification in the except block.
            """
            client = self._client
            next_batch = await client.sync_store.get_next_batch()
            while not self._closing:
                try:
                    sync_data = await asyncio.wait_for(
                        client.sync(since=next_batch, timeout=30000),
                        timeout=45.0,
                    )

                    # nio returns typed SyncError objects (not exceptions) for
                    # auth failures. Only its structured errcode is terminal;
                    # response message text is not part of classification.
                    sync_errcode = str(
                        getattr(sync_data, "errcode", "") or ""
                    ).strip().upper()
                    if sync_errcode == "M_UNKNOWN_TOKEN":
                        logger.error(
                            "Matrix: permanent auth error from sync (%s) "
                            "— stopping",
                            type(sync_data).__name__,
                        )
                        return

                    if not isinstance(sync_data, dict):
                        logger.warning(
                            "Matrix: non-dict sync response (%s) — retrying in 5s",
                            type(sync_data).__name__,
                        )
                        await asyncio.sleep(5)
                        continue

                    if isinstance(sync_data, dict):
                        self._last_sync_ts = time.time()
                        rooms_join = sync_data.get("rooms", {}).get("join", {})
                        if rooms_join:
                            self._joined_rooms.update(rooms_join.keys())
                            self._room_identities.clear()
                            self._room_identity_cached_at.clear()

                        nb = sync_data.get("next_batch")
                        if nb:
                            next_batch = nb
                            await client.sync_store.put_next_batch(nb)

                        try:
                            await self._dispatch_sync(sync_data)
                        except Exception as exc:
                            logger.warning(
                                "Matrix: sync event dispatch error (%s)",
                                type(exc).__name__,
                            )
                        self._schedule_pending_invite_joins(sync_data)
                        await asyncio.sleep(0)

                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    if self._closing:
                        return
                    # --- THE FIX ------------------------------------------
                    # Classify only typed Matrix errors and an exact structured
                    # M_UNKNOWN_TOKEN errcode, never message text or status
                    # alone. Transport failures must always be retried.
                    if _is_permanent_auth_error(exc):
                        logger.error(
                            "Matrix: permanent auth error (%s http_status=%s "
                            "errcode=%s) — stopping sync",
                            type(exc).__name__,
                            getattr(exc, "http_status", None),
                            str(getattr(exc, "errcode", "") or "").upper() or "-",
                        )
                        return
                    logger.warning(
                        "Matrix: sync error (%s) — retrying in 5s",
                        type(exc).__name__,
                    )
                    await asyncio.sleep(5)

    return PatchedMatrixAdapter


def register(ctx) -> None:
    """Plugin entry point — re-register 'matrix' with the patched adapter.

    IMPORTANT: bundled platform adapters load LAZILY. At register() time the
    module ``hermes_plugins.matrix_platform.adapter`` is NOT yet imported (the
    platform registry holds only a deferred loader for it), so importing it
    here raises ModuleNotFoundError and the whole plugin fails to load.

    Everything that touches the bundled module is therefore deferred behind
    callables that resolve on FIRST USE, by which point the gateway has
    materialized the bundled platform.
    """

    def _patched_upstream():
        """Load the pinned adapter sibling without registering another platform."""
        import importlib.util
        import sys
        from pathlib import Path

        module = sys.modules.get(PATCHED_MODULE_NAME)
        if module is not None:
            return module
        path = Path(__file__).with_name("patched_upstream.py")
        if not path.is_file():
            raise RuntimeError(
                "matrix-sync-auth-fix: patched_upstream.py is missing; "
                "refusing to construct MatrixAdapter"
            )
        spec = importlib.util.spec_from_file_location(PATCHED_MODULE_NAME, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(
                "matrix-sync-auth-fix: cannot load patched_upstream.py; "
                "refusing to construct MatrixAdapter"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[PATCHED_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(PATCHED_MODULE_NAME, None)
            raise
        return module

    _state: dict = {}

    def _patched_class():
        if "cls" not in _state:
            patched = _patched_upstream()
            base_cls = getattr(patched, "MatrixAdapter", None)
            required = {
                "MatrixAdapter": base_cls,
                "_build_hermes_olm_machine": getattr(
                    patched, "_build_hermes_olm_machine", None
                ),
                "recipient marker": getattr(
                    patched, "HERMES_E2EE_RECIPIENT_ENFORCEMENT_MARKER", None
                ),
                "_ensure_encrypted_room_ready": getattr(
                    base_cls,
                    "_ensure_encrypted_room_ready",
                    None,
                ),
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise RuntimeError(
                    "matrix-sync-auth-fix: patched Matrix base failed closed; "
                    f"missing {', '.join(missing)}"
                )
            actual_max = getattr(patched, "DEFAULT_MAX_MESSAGE_LENGTH", None)
            if actual_max != 16000:
                raise RuntimeError(
                    "matrix-sync-auth-fix: patched Matrix base failed closed; "
                    "max message length drifted "
                    f"(expected 16000, got {actual_max!r})"
                )
            parity = verify_parity()
            logger.info(
                "matrix-sync-auth-fix: parity check → %s", parity.get("status")
            )
            logger.info(
                "%s base_revision=%s",
                E2EE_SYNC_MARKER,
                str(getattr(patched, "HERMES_BASE_REVISION", "unknown"))[:12],
            )
            _state["cls"] = _build_patched_class(base_cls)
        return _state["cls"]

    def _build_patched_adapter(config):
        return _patched_class()(config)

    # Lazy trampolines: each resolves the sibling-pinned module on first call so that
    # nothing imports it at registration time.
    def _check_fn():
        return _patched_upstream().matrix_deps_present()

    def _ensure_deps_fn():
        return _patched_upstream().ensure_matrix_deps()

    def _is_connected(cfg):
        return _patched_upstream()._is_connected(cfg)

    def _setup_fn():
        return _patched_upstream().interactive_setup()

    def _apply_yaml_config_fn(yaml_cfg, platform_cfg):
        return _patched_upstream()._apply_yaml_config(yaml_cfg, platform_cfg)

    async def _standalone_sender_fn(*args, **kwargs):
        return await _patched_upstream()._standalone_send(*args, **kwargs)

    ctx.register_platform(
        name="matrix",
        label="Matrix",
        adapter_factory=_build_patched_adapter,
        check_fn=_check_fn,
        ensure_deps_fn=_ensure_deps_fn,
        is_connected=_is_connected,
        required_env=["MATRIX_HOMESERVER", "MATRIX_ACCESS_TOKEN"],
        install_hint="pip install 'mautrix[encryption]'",
        setup_fn=_setup_fn,
        apply_yaml_config_fn=_apply_yaml_config_fn,
        allowed_users_env="MATRIX_ALLOWED_USERS",
        allow_all_env="MATRIX_ALLOW_ALL_USERS",
        cron_deliver_env_var="MATRIX_HOME_ROOM",
        standalone_sender_fn=_standalone_sender_fn,
        # Mirrored here to avoid an early import. _patched_class() verifies the
        # sibling-pinned DEFAULT_MAX_MESSAGE_LENGTH before constructing an adapter.
        max_message_length=16000,
        emoji="🔐",
        allow_update_command=True,
    )
    logger.info(
        "matrix-sync-auth-fix: 'matrix' re-registered with PatchedMatrixAdapter "
        "(patched_upstream.py resolved lazily; no duplicate platform directory)"
    )
