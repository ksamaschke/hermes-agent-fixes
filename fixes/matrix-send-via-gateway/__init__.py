"""matrix-send-via-gateway — let scripts send through the RUNNING adapter.

THE PROBLEM
-----------
`hermes send --platform matrix` (and any script that builds its own mautrix
client) opens a SECOND E2EE client on the same crypto store the gateway is
already using. Two processes on one Olm account: the newcomer syncs, ratchets
the Olm sessions to every peer device, writes them to the store — and the
gateway keeps encrypting with the pre-ratchet state it holds in memory. From
that moment on, the room keys the gateway sends are unreadable for the peers:
"Unable to decrypt". Cron output delivered via `hermes send` reproduces this
every run.

THE RULE
--------
One Olm account, one process. Everything that wants to speak as the agent
goes THROUGH the gateway.

WHAT THIS DOES
--------------
Opens a Unix socket next to the profile's Matrix store:

    <HERMES_HOME>/platforms/matrix/send.sock

Protocol: one JSON object per line -> one JSON object per line.

    {"room_id": "!abc:server", "text": "...", "markdown": true}
    -> {"ok": true, "event_id": "$..."}   |   {"ok": false, "error": "..."}

The send goes through the adapter's own ``send()`` — same Megolm session,
same Olm ratchets, same code path as an agent reply. The CLI helper
``send.py`` in this directory is the drop-in replacement for `hermes send`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ATTR = "_hermes_send_via_gateway"
SOCK_NAME = "send.sock"


def socket_path(home: Path | None = None) -> Path:
    home = home or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home / "platforms" / "matrix" / SOCK_NAME


def _socket_for_adapter(adapter: Any) -> Path:
    """One socket per profile: derive it from the adapter's own crypto store
    location so a multiplexed gateway (several profiles, one process) gets a
    distinct socket per Matrix account."""
    # Class globals are the module the class was actually defined in, even if
    # the module was loaded under a per-profile name.
    for klass in type(adapter).__mro__:
        db = getattr(klass, "__init__", None) and klass.__init__.__globals__.get("_CRYPTO_DB_PATH")
        if db:
            return Path(db).parent.parent / SOCK_NAME
    return socket_path()


class _Server:
    def __init__(self, adapter: Any, path: Path) -> None:
        self.adapter = adapter
        self.path = path
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        # Deterministic instead of guessed: log where this profile listens.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))
        os.chmod(self.path, 0o600)
        log.info("matrix-send-via-gateway: listening on %s", self.path)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10)
            req = json.loads(raw.decode("utf-8"))
            room_id = str(req["room_id"])
            text = str(req["text"])
            if not text.strip():
                raise ValueError("empty text")
            result = await self.adapter.send(room_id, text, metadata=req.get("metadata"))
            ok = bool(getattr(result, "success", False))
            resp = {"ok": ok, "event_id": getattr(result, "message_id", None)}
            if not ok:
                resp["error"] = str(getattr(result, "error", "send failed"))
        except Exception as e:  # noqa: BLE001
            resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        try:
            await writer.drain()
        finally:
            writer.close()


def _install(native: Any, adapter: Any) -> bool:
    if getattr(native, _ATTR, None) is not None:
        return False
    srv = _Server(adapter, _socket_for_adapter(adapter))
    setattr(native, _ATTR, srv)
    loop = asyncio.get_event_loop()
    loop.create_task(srv.start())
    return True


def register(ctx: Any) -> None:
    def factory(native: Any, adapter: Any) -> Any:
        try:
            _install(native, adapter)
        except Exception:
            log.exception("matrix-send-via-gateway: install failed; scripts fall back to nothing")
        return None

    ctx.register_platform_handler("matrix", factory)
