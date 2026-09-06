"""Recover undecryptable Matrix messages by requesting the missing room key.

THE PROBLEM
-----------
mautrix's DecryptionDispatcher.handle() does this on failure:

    except DecryptionError as e:
        self.client.crypto_log.warning(f"Failed to decrypt {evt.event_id}: {e}")
        return                      # <- message dropped, permanently

No key request, no retry, no queue. Any moment where a room key does not
arrive -- a restart mid-delivery, a wedged olm session, a peer that sent
before our device keys were up -- costs that message forever. The operator
sees the agent go silent while the logs look healthy: it IS syncing, it just
cannot read anything, and it never asks anyone for help.

mautrix already ships the recovery primitive (OlmMachine.request_room_key,
the m.room_key_request / m.forwarded_room_key flow). Nothing calls it.
This plugin subclasses the stock dispatcher and calls it.

WHAT IT DOES
------------
Replaces DecryptionDispatcher with a subclass that, on SessionNotFound:

  1. asks the sender's devices (and our own) to forward the session key,
  2. waits for it (bounded), then
  3. retries decryption and dispatches the event normally.

Everything downstream -- routing, the agent turn, the reply -- runs exactly
as if decryption had succeeded first time, because we re-enter the same
dispatch call the stock dispatcher uses.

WHAT IT MUST NOT DO
-------------------
It does not decrypt to-device events itself. An olm message decrypts exactly
once; doing that consumes the m.room_key payloads inside and breaks
decryption for every other conversation on the gateway. That mistake is how
this gap was found -- do not reintroduce it. Key requests ride the normal
to-device path and are handled by mautrix's own OlmMachine.

Idempotence: each (room, session) is requested at most once per process, so
a burst of undecryptable events from one session yields one request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Bounds the case where nobody answers. Online peers reply in well under a
# second; this is not a latency budget, it is a give-up point.
KEY_WAIT_SECONDS = 12.0

_REQUESTED_MAX = 2048


def _build_dispatcher():
    """Build the dispatcher subclass against the installed mautrix."""
    from mautrix.client.encryption_manager import DecryptionDispatcher
    from mautrix.errors import DecryptionError, SessionNotFound

    class KeyRecoveringDispatcher(DecryptionDispatcher):
        """DecryptionDispatcher that asks for keys it is missing."""

        def __init__(self, client: Any) -> None:
            super().__init__(client)
            self._requested: set[tuple[str, str]] = set()

        def _first_time(self, room_id: Any, session_id: Any) -> bool:
            key = (str(room_id), str(session_id))
            if key in self._requested:
                return False
            if len(self._requested) >= _REQUESTED_MAX:
                self._requested.clear()
            self._requested.add(key)
            return True

        async def handle(self, evt: Any) -> None:
            crypto = self.client.crypto
            try:
                decrypted = await crypto.decrypt_megolm_event(evt)
            except SessionNotFound as e:
                await self._recover(evt, e)
                return
            except DecryptionError as e:
                # Not a missing key (bad index, replay, ...): stock behaviour.
                self.client.crypto_log.warning(
                    f"Failed to decrypt {evt.event_id}: {e}"
                )
                return
            self.client.dispatch_event(decrypted, evt.source)

        async def _recover(self, evt: Any, err: Any) -> None:
            session_id = getattr(err, "session_id", None)
            if not session_id or not self._first_time(evt.room_id, session_id):
                return

            # sender_key is deprecated in Matrix 1.3 and warns on access;
            # read the attribute the error stores it in directly.
            sender_key = getattr(err, "_sender_key", None) or getattr(
                evt.content, "sender_key", None
            )

            # The sender holds the session. Our own other devices may have
            # received the key even when this device did not.
            from_devices: dict[Any, list[Any]] = {evt.sender: []}
            own = getattr(self.client, "mxid", None)
            if own and own != evt.sender:
                from_devices[own] = []

            self.client.crypto_log.info(
                f"key-recovery: requesting session {session_id} in {evt.room_id} "
                f"from {', '.join(str(u) for u in from_devices)}"
            )
            try:
                got = await self.client.crypto.request_room_key(
                    evt.room_id, sender_key, session_id, from_devices,
                    timeout=KEY_WAIT_SECONDS,
                )
            except Exception:
                log.exception("key-recovery: request failed for %s", session_id)
                return

            if not got:
                self.client.crypto_log.warning(
                    f"key-recovery: nobody forwarded session {session_id} within "
                    f"{KEY_WAIT_SECONDS:.0f}s; {evt.event_id} stays unreadable"
                )
                return

            try:
                decrypted = await self.client.crypto.decrypt_megolm_event(evt)
            except Exception:
                log.exception(
                    "key-recovery: key arrived but decryption still failed for %s",
                    evt.event_id,
                )
                return

            self.client.crypto_log.info(
                f"key-recovery: recovered {evt.event_id} in {evt.room_id}"
            )
            self.client.dispatch_event(decrypted, evt.source)

    return KeyRecoveringDispatcher


def _install(native: Any) -> bool:
    from mautrix.client.encryption_manager import DecryptionDispatcher

    if not getattr(native, "crypto", None):
        return False

    recovering = _build_dispatcher()
    # Swap: drop the stock dispatcher's handler registration, add ours.
    native.remove_dispatcher(DecryptionDispatcher)
    native.add_dispatcher(recovering)
    log.info("matrix-key-recovery: active (missing room keys are now requested)")
    return True


def register(ctx: Any) -> None:
    def factory(native: Any, adapter: Any) -> Any:
        try:
            _install(native)
        except Exception:
            log.exception("matrix-key-recovery: install failed")
        return None

    ctx.register_platform_handler("matrix", factory)
