"""Identity guard — refuse to replace a device's Olm identity on the server.

WHY THIS EXISTS
---------------
A Matrix device id is only a label. The thing peers actually trust is the
device's Olm identity (curve25519/ed25519 pair) that lives in the local
crypto store. When that store is recreated -- moved, wiped, opened under a
different pickle key -- mautrix creates a brand-new Olm account and
``_share_keys`` uploads its keys under the SAME device id, silently replacing
the identity the server had.

Peers do not notice. Their Olm sessions with that device id still point at the
old identity, so every room key they send is encrypted for a key the agent no
longer owns. The agent sees ``KeyError: <its own new identity>`` on each
to-device event, never receives a room key from those peers again, and
"Unable to decrypt" becomes permanent for everyone who talked to the old
identity. Only a NEW device id makes peers refetch keys and rebuild sessions.

This guard hooks OlmMachine._share_keys. Before an initial device-key upload
(``account.shared`` is False) it queries the server for the device's current
keys. If the server already advertises a DIFFERENT identity for this device
id, the upload is refused and startup fails loudly with instructions, instead
of hijacking the device. A device with no keys on the server, or with the
same keys, passes through untouched.

Plugin-only: wraps mautrix at runtime, patches nothing in the checkout.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_PATCHED_FLAG = "_hermes_identity_guard_patched"


class IdentityMismatch(RuntimeError):
    """The server holds a different Olm identity for this device id."""


async def server_identity(client: Any, user_id: str, device_id: str) -> dict:
    """Return the ``keys`` block the server has for ``device_id`` ({} if none)."""
    resp = await client.query_keys({user_id: [device_id]})
    device_keys = getattr(resp, "device_keys", None) or {}
    per_user = device_keys.get(user_id) or {}
    entry = per_user.get(device_id)
    if entry is None:
        return {}
    keys = getattr(entry, "keys", None)
    if keys is None and isinstance(entry, dict):
        keys = entry.get("keys")
    return {str(k): str(v) for k, v in (keys or {}).items()}


def _local_identity(account: Any, device_id: str) -> dict:
    ik = account.identity_keys
    return {
        f"curve25519:{device_id}": ik["curve25519"],
        f"ed25519:{device_id}": ik["ed25519"],
    }


async def check_identity(machine: Any) -> None:
    """Raise IdentityMismatch if uploading would replace the server identity."""
    account = machine.account
    if account.shared:
        return  # not an initial upload; nothing to guard
    client = machine.client
    user_id, device_id = str(client.mxid), str(client.device_id)
    try:
        remote = await server_identity(client, user_id, device_id)
    except Exception as exc:  # network trouble must not mask a real upload
        log.warning(
            "matrix-store-guard: identity check skipped, keys/query failed (%s)",
            type(exc).__name__,
        )
        return
    if not remote:
        return  # fresh device: first upload is legitimate
    local = _local_identity(account, device_id)
    if all(remote.get(k) == v for k, v in local.items()):
        return  # same identity, e.g. store restored from backup
    raise IdentityMismatch(
        f"device {device_id} already has a different Olm identity on the "
        f"server (server ed25519 {remote.get(f'ed25519:{device_id}', '?')[:12]}…, "
        f"local {local[f'ed25519:{device_id}'][:12]}…). Refusing to replace it: "
        "peers would keep encrypting to the old identity and this agent could "
        "never read their messages again. Either restore the previous crypto "
        "store, or log in with a NEW device id (new access token) so peers "
        "rebuild their sessions."
    )


def _patch_machine_class() -> bool:
    from mautrix.crypto.machine import OlmMachine

    if getattr(OlmMachine, _PATCHED_FLAG, False):
        return False

    original = OlmMachine._share_keys

    async def share_keys_with_guard(self: Any, current_otk_count: Any) -> None:
        await check_identity(self)
        await original(self, current_otk_count)

    share_keys_with_guard.__doc__ = original.__doc__
    OlmMachine._share_keys = share_keys_with_guard  # type: ignore[method-assign]
    setattr(OlmMachine, _PATCHED_FLAG, True)
    return True


def install() -> bool:
    try:
        if _patch_machine_class():
            log.info(
                "matrix-store-guard: identity guard active (a device's Olm "
                "identity is never silently replaced on the server)"
            )
            return True
    except Exception:
        log.exception("matrix-store-guard: identity guard install failed")
    return False
