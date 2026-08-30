"""Supported user override for Hermes' bundled Matrix platform adapter.

Hermes discovers this explicitly enabled user plugin after the bundled Matrix
platform. Its ``register`` function replaces the registered ``matrix`` platform
factory through Hermes' ownership ledger without modifying the checkout. All
behavior is inherited from the bundled adapter except the sync-loop
authentication classifier.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from mautrix.errors import MUnknownToken, MatrixRequestError
from plugins.platforms.matrix import adapter as upstream

logger = logging.getLogger(__name__)

OVERRIDE_MARKER = "matrix-sync-auth-type-aware-v2"


def _is_permanent_sync_auth_error(exc: BaseException) -> bool:
    """Return true only for a structured Matrix unknown-token failure.

    Connection errors and generic exceptions are deliberately retryable even
    when their free-form text contains ``401``, ``403``, ``unauthorized``, or
    ``forbidden``. The homeserver's typed M_UNKNOWN_TOKEN response is the
    reliable signal that the active access token can no longer sync.
    """

    return isinstance(exc, MUnknownToken) or (
        isinstance(exc, MatrixRequestError)
        and str(getattr(exc, "errcode", "")).upper() == "M_UNKNOWN_TOKEN"
    )


class MatrixAdapter(upstream.MatrixAdapter):
    """Bundled Matrix adapter with type-aware sync auth classification."""

    async def _sync_loop(self) -> None:
        """Continuously sync with the homeserver."""
        client = self._client
        # Resume from the token stored during the initial sync.
        next_batch = await client.sync_store.get_next_batch()
        while not self._closing:
            try:
                # Wrap in asyncio.wait_for to guard against TCP-level hangs
                # that the Matrix long-poll timeout cannot catch. Long-poll
                # is 30s, so 45s gives 15s slack for network drain.
                sync_data = await asyncio.wait_for(
                    client.sync(
                        since=next_batch,
                        timeout=30000,
                    ),
                    timeout=45.0,
                )

                # A returned error object is terminal only when its
                # machine-readable Matrix errcode says M_UNKNOWN_TOKEN.
                # Human-readable message text is never an auth decision.
                sync_errcode = getattr(sync_data, "errcode", None)
                sync_message = getattr(sync_data, "message", None)
                unknown_token = (
                    isinstance(sync_errcode, str)
                    and sync_errcode.upper() == "M_UNKNOWN_TOKEN"
                )
                if unknown_token:
                    logger.error(
                        "Matrix: permanent M_UNKNOWN_TOKEN response from sync: %s — stopping",
                        sync_message or sync_errcode,
                    )
                    return

                if not isinstance(sync_data, dict):
                    response_kind = sync_errcode or type(sync_data).__name__
                    logger.warning(
                        "Matrix: non-success sync response (%s) — retrying in 5s",
                        response_kind,
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
                        logger.warning("Matrix: sync event dispatch error: %s", exc)
                    self._schedule_pending_invite_joins(sync_data)
                    await asyncio.sleep(0)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self._closing:
                    return
                if _is_permanent_sync_auth_error(exc):
                    logger.error(
                        "Matrix: permanent M_UNKNOWN_TOKEN exception from sync: %s — stopping",
                        exc,
                    )
                    return
                logger.warning("Matrix: sync error: %s — retrying in 5s", exc)
                await asyncio.sleep(5)


def _build_adapter(config: Any) -> MatrixAdapter:
    logger.info(
        "Matrix user plugin override active (%s): structured M_UNKNOWN_TOKEN "
        "classification; transport errors remain retryable",
        OVERRIDE_MARKER,
    )
    return MatrixAdapter(config)


def register(ctx: Any) -> None:
    """Register all upstream Matrix metadata with the override factory.

    Calling the upstream registration function keeps platform capabilities,
    setup hooks, dependency checks, and future metadata aligned with the
    installed Hermes revision. The temporary factory substitution is captured
    by the registry as a function object and is restored immediately afterward.
    """

    original_factory = upstream._build_adapter
    try:
        upstream._build_adapter = _build_adapter
        upstream.register(ctx)
    finally:
        upstream._build_adapter = original_factory
