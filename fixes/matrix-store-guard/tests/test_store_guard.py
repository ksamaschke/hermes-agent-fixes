"""Offline tests for matrix-store-guard against the installed mautrix.

Run:  python tests/test_store_guard.py

Uses a real SQLite database with mautrix's crypto_account schema, so the SQL
is exercised for real. No network, no gateway, no live crypto store.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "matrix-store-guard" / "__init__.py"

OWNER = "@agnes:example.org"
FOREIGN = "@ponder:example.org"


def _load():
    spec = importlib.util.spec_from_file_location("matrix_store_guard_undertest", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeDB:
    """Minimal asyncpg-shaped wrapper over sqlite3 ($1 -> ?)."""

    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE crypto_account ("
            " account_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,"
            " shared BOOLEAN NOT NULL, sync_token TEXT NOT NULL, account BLOB NOT NULL)"
        )

    def _sql(self, q: str) -> str:
        for i in range(9, 0, -1):
            q = q.replace(f"${i}", "?")
        return q

    async def fetch(self, q, *a):
        return [dict(r) for r in self.conn.execute(self._sql(q), a).fetchall()]

    async def execute(self, q, *a):
        self.conn.execute(self._sql(q), a)
        self.conn.commit()

    def add(self, account_id: str, device_id: str) -> None:
        self.conn.execute(
            "INSERT INTO crypto_account VALUES (?,?,?,?,?)",
            (account_id, device_id, 1, "tok", b"pickle"),
        )
        self.conn.commit()

    def accounts(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT account_id FROM crypto_account")]


def _db() -> FakeDB:
    return FakeDB(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)


def test_removes_foreign_account_keeps_owner():
    mod = _load()
    db = _db()
    db.add(OWNER, "agent-agnes")
    db.add(FOREIGN, "agent-ponder")

    removed = asyncio.run(mod.purge_foreign_accounts(db, OWNER))

    assert removed == [FOREIGN], f"should remove only the foreign row, got {removed}"
    assert db.accounts() == [OWNER], f"owner must survive, store has {db.accounts()}"
    print("PASS removes foreign account, keeps owner")


def test_clean_store_untouched():
    mod = _load()
    db = _db()
    db.add(OWNER, "agent-agnes")

    removed = asyncio.run(mod.purge_foreign_accounts(db, OWNER))

    assert removed == [], "a clean store must not be modified"
    assert db.accounts() == [OWNER]
    print("PASS clean store untouched")


def test_never_empties_store_without_owner_id():
    """Safety: an unknown owner must never cause a wipe."""
    mod = _load()
    db = _db()
    db.add(OWNER, "agent-agnes")
    db.add(FOREIGN, "agent-ponder")

    removed = asyncio.run(mod.purge_foreign_accounts(db, ""))

    assert removed == [], "empty owner id must be a no-op"
    assert set(db.accounts()) == {OWNER, FOREIGN}, "nothing may be deleted"
    print("PASS no owner id -> no deletion")


def test_survives_broken_db():
    """A store that does not match expectations is left alone, not crashed on."""
    mod = _load()

    class Broken:
        async def fetch(self, *a):
            raise RuntimeError("no such table")

        async def execute(self, *a):
            raise AssertionError("must not delete after a failed check")

    removed = asyncio.run(mod.purge_foreign_accounts(Broken(), OWNER))
    assert removed == []
    print("PASS broken store tolerated")


def test_patches_real_mautrix_store():
    """The wrap must apply to the real PgCryptoStore and be idempotent."""
    from mautrix.crypto.store.asyncpg import PgCryptoStore

    mod = _load()
    original = PgCryptoStore.open
    try:
        assert mod._patch_store_class() is True, "first patch must apply"
        assert PgCryptoStore.open is not original, "open must be wrapped"
        assert mod._patch_store_class() is False, "second patch must be a no-op"
        print("PASS patches real PgCryptoStore, idempotent")
    finally:
        PgCryptoStore.open = original
        if hasattr(PgCryptoStore, mod._PATCHED_FLAG):
            delattr(PgCryptoStore, mod._PATCHED_FLAG)


def test_guard_runs_before_open():
    """Cleaning must happen before the store is opened, not after."""
    from mautrix.crypto.store.asyncpg import PgCryptoStore

    mod = _load()
    original = PgCryptoStore.open
    order: list[str] = []

    async def fake_open(self):
        order.append("open")

    try:
        PgCryptoStore.open = fake_open
        mod._patch_store_class()

        db = _db()
        db.add(OWNER, "agent-agnes")
        db.add(FOREIGN, "agent-ponder")

        real_purge = mod.purge_foreign_accounts

        async def traced(d, o):
            order.append("purge")
            return await real_purge(d, o)

        mod.purge_foreign_accounts = traced

        store = PgCryptoStore.__new__(PgCryptoStore)
        store.db = db
        store.account_id = OWNER
        asyncio.run(PgCryptoStore.open(store))

        assert order == ["purge", "open"], f"wrong order: {order}"
        assert db.accounts() == [OWNER]
        print("PASS purge runs before open")
    finally:
        PgCryptoStore.open = original
        if hasattr(PgCryptoStore, mod._PATCHED_FLAG):
            delattr(PgCryptoStore, mod._PATCHED_FLAG)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {exc}")
    print("\nall passed" if not fails else f"\n{fails} failed")
    sys.exit(1 if fails else 0)
