# matrix-free-room-silence

## Problem

In a Matrix room listed in `matrix.free_response_rooms` the agent reads every
message and is told to answer only when the message concerns it. When it
correctly decides "not for me" and returns `NO_REPLY`, Hermes posts instead:

> ⚠️ The model returned only a silence marker for a message that needed a reply.

Hermes keeps a bare silence marker only when `silence_allowed()` sees
`reply_expected is False` (`gateway/response_filters.py`). The Slack adapter
sets `MessageEvent.reply_expected`; the Matrix adapter never does, so every
silence counts as a failure.

## Fix

A standalone user plugin. Its `pre_gateway_dispatch` hook sets
`event.reply_expected = False` for a Matrix message when all of these hold:

- the room (or a thread's parent room) is in the adapter's free-response rooms;
- it is not a DM;
- it does not mention the bot (same check the adapter uses, incl. `m.mentions`);
- it is not a reply to the bot;
- it is not a slash command;
- the adapter has not already decided `reply_expected`.

Everything else stays untouched, so a missing answer to a mention, reply or DM
still shows the warning. The hook never skips or rewrites a message.
No Hermes code is modified.

## Install

    cp -R plugin/matrix-free-room-silence ~/.hermes/plugins/
    hermes plugins enable matrix-free-room-silence

The running gateway reloads plugins on enable; the log shows
`matrix-free-room-silence: active`.

## Tests

    python tests/test_free_room_silence.py

## Verification status

- 12 offline tests green.
- Loaded in the HEX gateway (macOS, Hermes main `db48af245a`).
- Live acceptance pending: an unaddressed message in a free-response room must
  produce no reply and no warning; a mention must still get an answer.
