# Matrix room-key recovery

## Purpose

This plugin stops a Hermes Matrix agent from silently losing messages it cannot
decrypt. When a Megolm session is missing, it requests the key over the standard
`m.room_key_request` / `m.forwarded_room_key` flow, waits briefly, then retries
decryption and dispatches the event through the normal path.

It uses Hermes' supported user-plugin registration mechanism. It does **not**
modify the Hermes checkout, rotate the Matrix access token, replace the Matrix
device, or delete the E2EE crypto store.

## Symptom

The gateway looks healthy while the agent is effectively deaf:

- the gateway process runs and Matrix sync is connected;
- outbound sends work — the agent can still post;
- inbound messages in one or more rooms produce no reply at all;
- the log shows `Failed to decrypt $event: ... no session with given ID ...`;
- the same events fail again after every restart, forever.

Nothing recovers on its own, because nothing ever asks for the missing key.

## Root cause

`mautrix.client.encryption_manager.DecryptionDispatcher.handle()` ends a failed
decryption like this:

```python
except DecryptionError as e:
    self.client.crypto_log.warning(f"Failed to decrypt {evt.event_id}: {e}")
    return
```

The event is dropped. There is no key request, no retry, and no queue, so a
single gap in key delivery is permanent for those messages. Causes of such a gap
include a restart mid-delivery, a wedged Olm session, or a peer that encrypted
before our device keys were published.

mautrix already ships the recovery primitive — `OlmMachine.request_room_key`,
implementing the spec's key-request flow. Nothing in the default path calls it.

The important distinction is:

- **trigger:** one missed room key (restart, wedged session, key-share race);
- **persistent failure:** the dispatcher discards the event instead of
  requesting the key, so every later retry of the same event fails identically.

## What the plugin does

Replaces the dispatcher with a subclass that, on `SessionNotFound`:

1. asks the sender's devices (and our own other devices) to forward the session;
2. waits for the key, bounded by `KEY_WAIT_SECONDS` (default 12s);
3. retries decryption and dispatches the event through the stock path.

Downstream behaviour — routing, the agent turn, the reply — is unchanged,
because recovery re-enters the same dispatch call the stock dispatcher uses.

Each `(room_id, session_id)` is requested at most once per process, so a burst of
undecryptable events from one session produces exactly one request.

## Safety invariants

- **It must never decrypt to-device events itself.** An Olm message decrypts
  exactly once; consuming it destroys the `m.room_key` payloads inside and breaks
  decryption for every other conversation on the gateway. An earlier attempt to
  "help" by decrypting to-device events directly caused precisely that outage.
  Key requests ride the normal to-device path and stay with mautrix's OlmMachine.
- No upstream Hermes or mautrix file is patched or copied.
- No crypto store is deleted, reset, or migrated.
- A key that no peer still holds stays undecryptable; the plugin gives up
  quietly rather than looping.

## Requirements

- A Hermes gateway with Matrix E2EE enabled.
- mautrix exposing `DecryptionDispatcher` and `OlmMachine.request_room_key`
  (verified against the mautrix version bundled with Hermes as of 2026-09).

## Installation

Copy the plugin directory into the Hermes plugin path and enable it:

```bash
cp -r plugin/matrix-key-recovery ~/.hermes/plugins/
```

```yaml
# ~/.hermes/config.yaml
plugins:
  enabled:
    - matrix-key-recovery
```

Restart the gateway. Confirm activation in the log:

```text
matrix-key-recovery: active (missing room keys are now requested)
[Matrix] Wired native handlers from plugin 'matrix-key-recovery'
```

## Verification status

Honest status as of 2026-09-06:

- **Loads and activates on a live gateway:** confirmed. The activation lines
  above appear on every start, for both agent accounts on the test host.
- **Offline tests against the installed mautrix:** 5 cases pass — dispatcher
  subclassing, single-request idempotence per `(room, session)`, bounded
  dedupe set, bounded wait on no answer, and the to-device safety invariant.
- **Recovery of a live missed key end-to-end:** *not yet observed.* On the test
  host the outstanding undecryptable events came from sessions no peer still
  held, so no forward could arrive. The request path was exercised; a successful
  forward was not.
- **Not a fix for key delivery itself.** If keys never reach the agent because
  of a broken crypto store or a duplicated Olm account, fix that first — this
  plugin only recovers individual gaps.

## Provenance

Written for a two-agent Hermes gateway where one agent stopped answering in an
encrypted DM while the gateway reported healthy. The dispatcher's silent `return`
was identified as the reason no recovery ever happened.
