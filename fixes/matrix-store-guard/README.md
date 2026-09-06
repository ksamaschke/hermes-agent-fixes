# Matrix store guard

## Purpose

Keeps each Hermes profile's Matrix crypto store limited to exactly one Olm
account: the one that store belongs to. Foreign account rows are removed at
startup, before the store is opened.

It is a plugin. It patches nothing in the Hermes checkout, copies no upstream
file into the platform adapter, and never touches sessions, devices, or keys.

## Symptom

An agent goes deaf in encrypted rooms while everything looks healthy:

- the gateway runs, Matrix sync is connected, outbound sends work;
- inbound encrypted messages are never answered;
- `mau.crypto: No one-time keys nor device keys got when trying to share keys`
  repeats, often every few minutes;
- clients cannot decrypt what the agent sends, and the agent cannot decrypt
  what they send;
- every restart produces a new Megolm session that nobody can open.

## Root cause

A crypto store is per profile and is expected to hold one account. If profile
and account wiring is ever crossed — one agent starting against another
profile's store — the visiting agent writes its own Olm account row into that
store. Nothing removes it afterwards, and nothing warns about it.

That orphan row is not inert. An Olm account owns the one-time-key counter for
its matrix device. A second copy of the same account, living in a second store,
publishes and rotates one-time keys for that same device independently. Peers
claim a key from the server, but the running account never held its private
half, so:

- no Olm channel can be established with that peer;
- room keys are therefore never delivered;
- messages stay undecryptable in both directions.

The store that is actively used looks completely normal. The damage comes from
the copy elsewhere, which is why this is easy to misdiagnose as a broken
device, a bad token, or a gateway problem.

## What the plugin does

Wraps `mautrix.crypto.store.asyncpg.PgCryptoStore.open` and, before the store
is opened:

1. reads `crypto_account` rows whose `account_id` is not the store's owner;
2. deletes those rows, logging each removal with both account ids;
3. proceeds to the original `open()`.

The wrap is idempotent, so repeated plugin loads do not stack.

## Safety invariants

- **Only foreign rows are deleted.** The owner's account is matched by
  `account_id` and always survives.
- **An unknown owner is a no-op.** With no `account_id`, nothing is deleted —
  a missing identity must never cause a wipe.
- **A store that does not match expectations is left alone.** If the schema
  query fails, the plugin gives up quietly instead of deleting anything.
- **Sessions, devices and keys are never touched.** Only `crypto_account` rows.
- A failure inside the guard never blocks startup.

## Requirements

- A Hermes gateway with Matrix E2EE enabled.
- mautrix exposing `PgCryptoStore` (verified against the mautrix version
  bundled with Hermes as of 2026-09).

## Installation

```bash
cp -r plugin/matrix-store-guard ~/.hermes/plugins/
```

```yaml
# ~/.hermes/config.yaml
plugins:
  enabled:
    - matrix-store-guard
```

Restart the gateway.

## Verification status

Honest status as of 2026-09-06, on a live two-agent gateway:

- **Offline tests against the installed mautrix:** 6 pass — foreign row
  removed while the owner survives, clean store untouched, empty owner id is a
  no-op, broken store tolerated, the real `PgCryptoStore` is patched
  idempotently, and the purge provably runs *before* `open()`.
- **Live effect confirmed by canary, three times.** A foreign account row was
  inserted into a running profile's store and the gateway restarted; the row
  was gone afterwards, while the profile's own account survived. A control run
  without a restart left the row in place, which rules out coincidence.
- **Live symptom cleared.** On the affected host the repeating
  "No one-time keys nor device keys" warning dropped to zero after the
  duplicate accounts were removed, and both agents resumed answering in
  encrypted rooms.
- **Not a repair tool for lost keys.** Messages encrypted while the store was
  broken stay unreadable if no peer still holds the session.

## Provenance

Written after a two-agent Hermes gateway where one agent stopped answering in
encrypted rooms. Both agents' Olm accounts were found in both profile stores,
byte-identical, left over from a window of crossed wiring days earlier.
