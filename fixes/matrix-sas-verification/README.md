# Matrix SAS verification

## Purpose

Lets a human verify the agent's Matrix device from a client ("Verify by
emoji"). The agent has no screen, so it cannot confirm a verification itself;
this answers the `m.key.verification.*` flow on its behalf and posts the emoji
short-auth-string into the room so the user can compare and confirm.

It is a plugin. It patches nothing in the Hermes checkout and copies no file
into the platform adapter.

## Symptom

- "Verify by emoji" against the bot's device gets no response and times out;
- the bot's device keeps the red / unverified shield in Element indefinitely;
- or the flow starts, emoji appear, and the client then aborts with
  `m.mismatched_sas`.

## What the plugin does

On Matrix connect with E2EE enabled, it attaches a responder to the live
mautrix client and registers handlers for the full flow — `request`, `ready`,
`start`, `accept`, `key`, `mac`, `done`, `cancel` — over **both** transports:

- **to-device**, used by older clients;
- **in-room** via `m.relates_to`, used by Element X (MSC 2241).

Installation is idempotent, so a repeated wire-up does not register handlers
twice.

## Invariants

- **In-room verification events must be sent unencrypted.** Encrypting them
  leaves the initiating client unable to read its own handshake, which surfaces
  as `m.mismatched_sas`. The handler passes `disable_encryption=True` on that
  send path, and a test pins it.
- **A failure must never break messaging.** If anything in the install path
  raises, it is swallowed and the agent keeps running — unverified, but fully
  functional.
- Without E2EE there is nothing to verify, and the plugin skips quietly.

## Requirements

- A Hermes gateway with Matrix E2EE enabled.
- A device id the client can verify against (`MATRIX_DEVICE_ID`).

## Installation

```bash
cp -r plugin/matrix-sas-verification ~/.hermes/plugins/
```

```yaml
# ~/.hermes/config.yaml
plugins:
  enabled:
    - matrix-sas-verification
```

Restart the gateway and confirm in the log:

```text
Matrix: SAS verification handler registered (emoji compare, to_device + in-room)
matrix-sas-verification: active (emoji verification answered)
[Matrix] Wired native handlers from plugin 'matrix-sas-verification'
```

## Verification status

Honest status as of 2026-09-06:

- **Loads and attaches on a live gateway:** confirmed. The three log lines
  above appear on start, and the responder registers 17 event handlers across
  both transports.
- **Offline tests against the installed mautrix:** 6 pass — handler constructs,
  installs on a crypto-enabled client, is idempotent, skips without crypto,
  covers both transports, sends in-room events with `disable_encryption=True`,
  and contains install failures.
- **A completed verification producing a green shield: NOT yet achieved.**
  On the test host the flow runs end to end in plaintext and the MACs are
  exchanged, but the initiating client still ends the flow with
  `m.mismatched_sas`. The cause is not yet established. Do not deploy this
  expecting a working green shield.

### Known open problem

The MAC step is where it fails. Two changes made while investigating are in
this code and are *not* proven to be correct or sufficient:

- the self-signing key was removed from the MAC key map, on the theory that the
  initiating client MACs only the device key and the master key;
- the ordering was changed so our MAC is sent before the peer's MAC is
  verified, and the signature upload happens after `done` rather than inside
  the time-critical handshake.

A stale-request guard is also included: verification requests older than ten
minutes are ignored, per spec. That one *is* confirmed — it stopped a loop
where a replayed request from hours earlier was answered on every sync,
posting a cancel into the room each time.

## Provenance

Written for a Hermes agent that could not be verified from Element. The
responder logic originates from the upstream Hermes SAS verification work; it
is packaged here as a plugin so no file in the Hermes checkout is modified.
