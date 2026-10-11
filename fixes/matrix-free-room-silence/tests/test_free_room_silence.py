"""Offline tests for matrix-free-room-silence. Run: python tests/test_free_room_silence.py"""
from __future__ import annotations

import enum
import importlib.util
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "matrix-free-room-silence" / "__init__.py"
spec = importlib.util.spec_from_file_location("mfrs_undertest", PLUGIN)
mfrs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mfrs)

BOT = "@hex:example.org"
FREE = "!free:example.org"
OTHER = "!other:example.org"


class Platform(enum.Enum):
    MATRIX = "matrix"
    SLACK = "slack"


@dataclass
class Source:
    platform: Platform = Platform.MATRIX
    chat_id: str = FREE
    chat_type: str = "group"
    parent_chat_id: Optional[str] = None
    profile: Optional[str] = None


@dataclass
class Event:
    text: str = "Albus, schau dir das an"
    source: Source = field(default_factory=Source)
    raw_message: dict = field(default_factory=lambda: {"body": "Albus, schau dir das an"})
    reply_to_author_id: Optional[str] = None
    reply_expected: Optional[bool] = None


class Adapter:
    _user_id = BOT
    _free_rooms = {FREE}

    def _content_mentions_bot(self, body, content):
        ids = (content.get("m.mentions") or {}).get("user_ids") or []
        return BOT in ids or "hex" in body.lower()


class Gateway:
    def __init__(self):
        self.adapters = {Platform.MATRIX: Adapter()}
        self._profile_adapters = {}


def run(ev, gw=None):
    mfrs._on_pre_gateway_dispatch(event=ev, gateway=gw or Gateway())
    return ev.reply_expected


class Tests(unittest.TestCase):
    def test_unaddressed_message_in_free_room_may_stay_silent(self):
        self.assertIs(run(Event()), False)

    def test_thread_in_free_room(self):
        self.assertIs(run(Event(source=Source(chat_id="$thread", chat_type="thread", parent_chat_id=FREE))), False)

    def test_mention_keeps_fallback(self):
        ev = Event(raw_message={"body": "x", "m.mentions": {"user_ids": [BOT]}})
        self.assertIsNone(run(ev))

    def test_reply_to_bot_keeps_fallback(self):
        self.assertIsNone(run(Event(reply_to_author_id=BOT)))

    def test_command_keeps_fallback(self):
        self.assertIsNone(run(Event(text="/status")))

    def test_dm_keeps_fallback(self):
        self.assertIsNone(run(Event(source=Source(chat_type="dm"))))

    def test_other_room_keeps_fallback(self):
        self.assertIsNone(run(Event(source=Source(chat_id=OTHER))))

    def test_other_platform_untouched(self):
        self.assertIsNone(run(Event(source=Source(platform=Platform.SLACK))))

    def test_adapter_verdict_is_not_overridden(self):
        self.assertIs(run(Event(reply_expected=True)), True)

    def test_missing_raw_content_keeps_fallback(self):
        self.assertIsNone(run(Event(raw_message=None)))

    def test_profile_adapter_is_used(self):
        gw = Gateway()
        gw.adapters = {}
        gw._profile_adapters = {"infra": {Platform.MATRIX: Adapter()}}
        self.assertIs(run(Event(source=Source(profile="infra")), gw), False)

    def test_hook_never_skips_or_rewrites(self):
        self.assertIsNone(mfrs._on_pre_gateway_dispatch(event=Event(), gateway=Gateway()))
        self.assertIsNone(mfrs._on_pre_gateway_dispatch(event=None, gateway=None))


if __name__ == "__main__":
    unittest.main()
