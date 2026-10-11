"""matrix-free-room-silence — allow deliberate silence in Matrix free-response rooms.

Hermes keeps a bare silence marker (NO_REPLY / [SILENT]) only when the platform
adapter says the message was not meant for the bot (``reply_expected is
False``). Otherwise it replaces the silence with a visible warning. The Matrix
adapter never sets ``reply_expected``, so an agent that reads a room without
@mention and correctly decides "not for me" posts that warning every time.

This plugin decides ``reply_expected`` for Matrix the way the Slack adapter
does: in a free-response room, a message that does not mention the bot and is
not a reply to the bot is marked ``reply_expected = False``. Everything else is
left untouched, so a mention, a reply to the bot, a slash command or a DM still
gets the visible fallback when the model goes silent.

Plugin-only: uses the documented ``pre_gateway_dispatch`` hook, mutates only
``event.reply_expected``, and never drops or rewrites a message.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def _matrix_adapter(gateway: Any, source: Any) -> Optional[Any]:
    """The Matrix adapter serving this event's profile (default profile otherwise)."""
    platform = getattr(source, "platform", None)
    profile = getattr(source, "profile", None)
    if profile:
        adapters = (getattr(gateway, "_profile_adapters", None) or {}).get(profile) or {}
        if platform in adapters:
            return adapters[platform]
    return (getattr(gateway, "adapters", None) or {}).get(platform)


def _mentions_bot(adapter: Any, raw: Any) -> bool:
    """True when the original event content mentions the bot. Unknown -> True (keep fallback)."""
    if not isinstance(raw, dict):
        return True
    check = getattr(adapter, "_content_mentions_bot", None)
    if not callable(check):
        return True
    try:
        return bool(check(raw.get("body", "") or "", raw))
    except Exception:
        log.debug("matrix-free-room-silence: mention check failed", exc_info=True)
        return True


def classify(event: Any, adapter: Any) -> Optional[bool]:
    """``False`` when the message is an unaddressed one in a free-response room, else ``None``."""
    source = getattr(event, "source", None)
    if source is None or getattr(source, "chat_type", "dm") == "dm":
        return None
    free_rooms = getattr(adapter, "_free_rooms", None) or set()
    room = getattr(source, "chat_id", None)
    if room not in free_rooms and getattr(source, "parent_chat_id", None) not in free_rooms:
        return None
    text = (getattr(event, "text", "") or "").lstrip()
    if text.startswith("/"):
        return None
    own_id = getattr(adapter, "_user_id", "") or ""
    if own_id and getattr(event, "reply_to_author_id", None) == own_id:
        return None
    if _mentions_bot(adapter, getattr(event, "raw_message", None)):
        return None
    return False


def _on_pre_gateway_dispatch(event: Any = None, gateway: Any = None, **_: Any) -> None:
    try:
        source = getattr(event, "source", None)
        platform = getattr(getattr(source, "platform", None), "value", None)
        if platform != "matrix" or getattr(event, "reply_expected", None) is not None:
            return None
        adapter = _matrix_adapter(gateway, source)
        if adapter is None:
            return None
        if classify(event, adapter) is False:
            event.reply_expected = False
            log.debug("matrix-free-room-silence: unaddressed message in %s may stay silent",
                      getattr(source, "chat_id", "?"))
    except Exception:
        log.exception("matrix-free-room-silence: classification failed; leaving event unchanged")
    return None  # never skip or rewrite


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    log.info("matrix-free-room-silence: active")
