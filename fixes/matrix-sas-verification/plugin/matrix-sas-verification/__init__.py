"""matrix-sas-verification — interactive emoji verification, as a plugin.

WHY THIS EXISTS
---------------
A Hermes Matrix agent has no screen, so it cannot click "verify" in a client.
Without a responder for the m.key.verification.* flow, a human starting
"Verify by emoji" against the bot's device gets no answer, the flow times out,
and the bot stays unverified -- the red shield in Element -- forever.

This ships the responder side of the Matrix m.sas.v1 flow (spec:
client-server-api SAS method) and registers it on the live mautrix client when
the Matrix platform comes up with E2EE enabled. Both transports are handled:
to-device (older clients) and in-room via m.relates_to (Element X, MSC 2241).

Plugin-only. It patches nothing in the Hermes checkout, copies no upstream
file into the platform adapter, and does not touch the crypto store. If
anything about the flow fails, the agent keeps running unverified rather than
breaking messaging.

IMPORTANT INVARIANT
-------------------
In-room verification events MUST be sent as plaintext. Encrypting them makes
the initiating client unable to read its own handshake, which surfaces as
m.mismatched_sas. The handler passes disable_encryption for those sends.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_ATTR = "_hermes_sas_handler"


def _install(native: Any, adapter: Any) -> bool:
    """Attach the SAS responder to a live, crypto-enabled mautrix client."""
    crypto = getattr(native, "crypto", None)
    if crypto is None:
        log.debug("matrix-sas-verification: no crypto on client, skipping")
        return False

    if getattr(native, _ATTR, None) is not None:
        return False

    from .handler import SasVerificationHandler

    handler = SasVerificationHandler(adapter, native, crypto)
    handler.register()
    setattr(native, _ATTR, handler)
    log.info("matrix-sas-verification: active (emoji verification answered)")
    return True


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""

    def factory(native: Any, adapter: Any) -> Any:
        try:
            _install(native, adapter)
        except Exception:
            log.exception(
                "matrix-sas-verification: install failed; agent continues "
                "unverified"
            )
        return None

    ctx.register_platform_handler("matrix", factory)
