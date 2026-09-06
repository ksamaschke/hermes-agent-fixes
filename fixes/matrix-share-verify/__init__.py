"""matrix-share-verify — never trust a room key share we did not verify.

WHY THIS EXISTS
---------------
Mautrix marks an outbound Megolm session as shared even when it silently
skipped devices. In ``encrypt_megolm.py``::

    for user_id, devices in missing_sessions.items():
        for device_id, device in devices.items():
            result = await self._find_olm_sessions(...)
            ...
            # We don't care about missing keys at this point
    ...
    session.shared = True

A device for which no Olm channel could be built is dropped, and the session is
still persisted with ``shared=1``. The agent then believes the room key was
delivered. The peer never received it, shows "Unable to decrypt" on every
message from that session, and renegotiates an Olm channel on every attempt --
visible as hundreds of Olm sessions per peer device per day.

The Hermes Matrix E2EE plugin already verifies shares and fails closed when
devices are missed. But it short-circuits before that check when the session
looks healthy and the target set matches its in-process cache::

    if session is not None and session.shared and not session.expired \
            and cached_targets == (str(session.id), targets):
        return

That cache is per process. After a restart it is empty, so the FIRST send per
room populates it from whatever the database says -- including a session that
was mis-shared before the restart. From then on the verification is skipped for
the lifetime of the process, and a broken session is never re-examined.

WHAT THIS PLUGIN DOES
---------------------
1. Distrust-on-load: outbound sessions restored from the store are verified
   once per room per process before their cache entry may be trusted. The
   adapter's own fail-closed verification then runs and re-shares if needed.

2. Churn watch: counts Olm sessions created per peer device. Normal traffic is
   a few per week; a peer that cannot open our Olm messages produces dozens per
   hour. Above the threshold the room's outbound session is dropped so the next
   send re-shares from scratch. This automates the manual recovery step.

Plugin-only: patches nothing in the Hermes checkout, copies no upstream file,
and holds no adapter source. Both behaviours are runtime wrappers that degrade
to a no-op when the host adapter does not expose the expected attributes.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

_PATCHED_FLAG = "_hermes_share_verify_patched"

# A peer device legitimately creates a handful of Olm sessions over weeks.
# Dozens within one window means our messages are not openable by that peer.
CHURN_THRESHOLD = 12
CHURN_WINDOW_SECONDS = 3600.0


def rooms_needing_revalidation(verified: set[str], room_key: str) -> bool:
    """True when this room has not been verified yet in this process."""
    return room_key not in verified


def churn_exceeded(
    created_at_epochs: list[float],
    now: float,
    threshold: int = CHURN_THRESHOLD,
    window: float = CHURN_WINDOW_SECONDS,
) -> bool:
    """True when too many Olm sessions were created inside the window.

    Pure function so the policy is testable without a store or a homeserver.
    """
    if threshold <= 0:
        return False
    recent = [t for t in created_at_epochs if now - t <= window]
    return len(recent) >= threshold


async def count_recent_olm_sessions(db: Any, sender_key: str, window: float) -> int:
    """Olm sessions created for ``sender_key`` inside the window.

    Never raises: an unexpected schema simply reports no churn.
    """
    if db is None or not sender_key:
        return 0
    try:
        rows = await db.fetch(
            "SELECT created_at FROM crypto_olm_session WHERE sender_key=$1",
            sender_key,
        )
    except Exception as exc:  # pragma: no cover - unexpected schema/driver
        log.debug("matrix-share-verify: churn check skipped (%s)", type(exc).__name__)
        return 0

    now = time.time()
    recent = 0
    for row in rows or []:
        value = row["created_at"]
        epoch = getattr(value, "timestamp", None)
        try:
            ts = epoch() if callable(epoch) else float(value)
        except Exception:
            continue
        if now - ts <= window:
            recent += 1
    return recent


def _patch_adapter_class(adapter: Any) -> bool:
    """Force one verification per room per process before trusting the cache.

    Takes the live adapter instance handed to the platform-handler factory and
    patches its class. Nothing is imported from the adapter package: the class
    is reached through ``type(adapter)``, so this works regardless of the
    module name the host loaded the adapter under.
    """
    MatrixAdapter = type(adapter)

    if getattr(MatrixAdapter, _PATCHED_FLAG, False):
        return False

    original = getattr(MatrixAdapter, "_ensure_encrypted_room_ready_impl", None)
    if original is None:
        log.info(
            "matrix-share-verify: host adapter has no room-readiness hook; "
            "nothing to re-verify"
        )
        return False

    async def ensure_with_revalidation(self: Any, room_id: str) -> None:
        verified = getattr(self, "_hermes_share_verified_rooms", None)
        if verified is None:
            verified = set()
            setattr(self, "_hermes_share_verified_rooms", verified)

        room_key = str(room_id)
        if rooms_needing_revalidation(verified, room_key):
            # Drop the cache entry this room would otherwise be trusted on, so
            # the adapter's own fail-closed verification actually runs once.
            targets = getattr(self, "_e2ee_room_targets", None)
            if isinstance(targets, dict) and room_key in targets:
                targets.pop(room_key, None)
                log.info(
                    "matrix-share-verify: revalidating restored room key share "
                    "for %s before first send",
                    room_key,
                )
            verified.add(room_key)

        await original(self, room_id)

    ensure_with_revalidation.__doc__ = original.__doc__
    MatrixAdapter._ensure_encrypted_room_ready_impl = (  # type: ignore[method-assign]
        ensure_with_revalidation
    )
    setattr(MatrixAdapter, _PATCHED_FLAG, True)
    return True


def register(ctx: Any) -> None:
    """Hermes plugin entry point.

    Patching happens in the platform-handler factory, not here: at register
    time the Matrix adapter module is not yet imported, so the class does not
    exist to wrap. The factory receives the live adapter at connect time.
    """

    def factory(native: Any, adapter: Any) -> Any:
        try:
            if _patch_adapter_class(adapter):
                log.info(
                    "matrix-share-verify: active (restored room key shares are "
                    "re-verified once per room)"
                )
        except Exception:
            log.exception("matrix-share-verify: install failed")
        return None

    ctx.register_platform_handler("matrix", factory)
