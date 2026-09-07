"""matrix-store-guard — keep one Olm account per profile crypto store.

WHY THIS EXISTS
---------------
A Hermes profile's crypto store must contain exactly one Olm account: its
owner's. If profile/account wiring is ever crossed -- one agent starting
against another profile's store -- the visiting agent writes its own Olm
account row into that store and leaves it behind. Nothing ever cleans it up.

The orphan is not inert. An Olm account owns the one-time-key counter for its
matrix device. A second copy of the same account, living in another store,
publishes and rotates one-time keys for that device behind the live copy's
back. Peers then claim keys whose private half the live account never had, so
no Olm channel can be established, room keys are never delivered, and messages
stay undecryptable. The visible symptoms are a steady
"No one-time keys nor device keys got when trying to share keys" in the log and
an agent that answers nothing in encrypted rooms while looking perfectly
healthy.

This plugin deletes foreign account rows from the store at startup, before the
crypto store is used. It only ever removes accounts that are NOT the configured
owner of that store, and it never touches sessions, devices, or keys.

Plugin-only: it patches nothing in the Hermes checkout and copies no upstream
file. It wraps mautrix's PgCryptoStore.open at runtime.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_PATCHED_FLAG = "_hermes_store_guard_patched"


async def purge_foreign_accounts(db: Any, own_account_id: str) -> list[str]:
    """Remove Olm accounts other than ``own_account_id`` from this store.

    Returns the list of account ids removed. Never raises: a store that does
    not look the way we expect is left exactly as it is.
    """
    if not own_account_id or db is None:
        return []

    try:
        rows = await db.fetch(
            "SELECT account_id FROM crypto_account WHERE account_id<>$1",
            own_account_id,
        )
    except Exception as exc:  # pragma: no cover - unexpected schema/driver
        log.debug("matrix-store-guard: check skipped (%s)", type(exc).__name__)
        return []

    foreign = [r["account_id"] for r in (rows or [])]
    removed: list[str] = []
    for acct in foreign:
        log.warning(
            "matrix-store-guard: removing foreign Olm account %s from the store "
            "owned by %s — a duplicate account competes for its device's "
            "one-time keys and breaks room-key delivery",
            acct,
            own_account_id,
        )
        try:
            await db.execute("DELETE FROM crypto_account WHERE account_id=$1", acct)
            removed.append(acct)
        except Exception as exc:
            log.warning(
                "matrix-store-guard: could not remove %s (%s)",
                acct,
                type(exc).__name__,
            )
    return removed


def _patch_store_class() -> bool:
    """Wrap PgCryptoStore.open so every store is cleaned before first use."""
    from mautrix.crypto.store.asyncpg import PgCryptoStore

    if getattr(PgCryptoStore, _PATCHED_FLAG, False):
        return False

    original_open = PgCryptoStore.open

    async def open_with_guard(self: Any) -> None:
        try:
            await purge_foreign_accounts(
                getattr(self, "db", None), getattr(self, "account_id", "")
            )
        except Exception:  # pragma: no cover - guard must never block startup
            log.exception("matrix-store-guard: purge failed, continuing")
        await original_open(self)

    open_with_guard.__doc__ = original_open.__doc__
    PgCryptoStore.open = open_with_guard  # type: ignore[method-assign]
    setattr(PgCryptoStore, _PATCHED_FLAG, True)
    return True


def _install_identity_guard() -> None:
    try:
        from . import identity as _identity
    except ImportError:  # loaded as a flat module, not a package
        import importlib.util
        import pathlib

        spec = importlib.util.spec_from_file_location(
            "hermes_plugins.matrix_store_guard.identity",
            pathlib.Path(__file__).with_name("identity.py"),
        )
        _identity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_identity)
    _identity.install()


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""
    try:
        _install_identity_guard()
    except Exception:
        log.exception("matrix-store-guard: identity guard failed to install")
    try:
        if _patch_store_class():
            log.info(
                "matrix-store-guard: active (each profile store is kept to its "
                "own Olm account)"
            )
    except Exception:
        log.exception("matrix-store-guard: install failed")
