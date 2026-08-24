# Matrix E2EE key delivery after reconnects

## Purpose

This document explains a recurring Hermes Matrix symptom for other Hermes instances:

- Hermes sends a valid `m.room.encrypted` event using Megolm;
- the receiving Element client shows **Waiting for the message**;
- the sender may log that a group session was shared even though no usable peer-device recipient was prepared.

The primary deliverable is the explanation and acceptance contract. The adapter and test patches in this directory are optional implementation components.

## Root cause

Matrix room encryption has two separate operations:

1. encrypting the event payload with the outbound Megolm session;
2. delivering that Megolm room key to the actual devices currently joined to the room.

A Hermes process can complete the first operation while the second is stale or empty. Reconnects are the dangerous boundary: peer device lists may remain cached, the crypto store may preserve an outbound session marked as shared, and Mautrix logs may describe the local share attempt more positively than the actual prepared to-device recipients justify.

This is not, by itself, evidence of:

- a missing recovery key;
- failed cross-signing verification;
- plaintext downgrade;
- a broken Synapse encryption configuration;
- a problem that containerization alone will solve.

Cross-signing and recovery material matter for trust and recovery. They do not replace outbound room-key delivery.

## Required behavior for every Hermes instance

Before sending an encrypted room event, the adapter must:

1. preserve the existing crypto store and Matrix device identity;
2. inspect the room encryption state authoritatively;
3. enumerate current joined peer users and device identities;
4. refresh peer device keys after reconnect and before a stale-cache window expires;
5. reuse or create the outbound Megolm session and explicitly share it with current eligible peer devices;
6. record the actual `(user, device, identity-key)` recipients prepared by the share path;
7. verify that the persisted outbound session is the same session that was shared and is marked shared;
8. fail closed on zero recipients, partial recipients, invalid device data, or crypto-state inspection errors;
9. avoid uploading media bytes until room state and key readiness have passed;
10. apply the same encrypted-send path to text, edits, reactions, notices, media, and files;
11. retain Mautrix's persisted `m.room_key_request` handling;
12. fence sends and key work against reconnect/disconnect lifecycle changes;
13. redact credentials and sensitive URL material from logs and user-visible errors.

A refresh interval of `0` means **refresh on every encrypted readiness check**. It must not disable the safety refresh.

## Optional mode

Optional mode may send plaintext only after the room is authoritatively confirmed to be unencrypted. These states must remain distinct:

- confirmed unencrypted room: the homeserver reports Matrix `M_NOT_FOUND` for `m.room.encryption`;
- encrypted room with no eligible peers: fail closed;
- malformed, empty, unavailable, or otherwise failed room-state inspection: fail closed.

A cached local answer is not enough for a plaintext-capable outbound decision.

## Standalone senders

A standalone cron sender that does not own the persistent crypto machine must not attempt encrypted-room delivery. It should query room encryption state first and refuse encrypted rooms. A 404 is accepted as an unencrypted-room result only when the JSON body contains the exact Matrix error code `M_NOT_FOUND`; arbitrary proxy or routing 404s are failures.

## Acceptance checklist

- [ ] Existing crypto database and Matrix device ID remain intact.
- [ ] A reconnect refreshes peer device lists before the next encrypted send.
- [ ] A new peer device is included without deleting or recreating the bot identity.
- [ ] A deleted or blacklisted peer device is not treated as an eligible target.
- [ ] Empty and partial device-key responses clear refresh state and fail closed.
- [ ] Zero prepared recipients prevent the encrypted event from being sent.
- [ ] Partial prepared recipients prevent the encrypted event from being sent.
- [ ] A changed outbound session ID or peer identity key invalidates the readiness cache.
- [ ] An encrypted media path validates state and key readiness before upload.
- [ ] Disconnect/reconnect cannot let an old operation send through a new client generation.
- [ ] Delayed invite joins and redactions cannot operate on a closed client.
- [ ] A persisted outbound session can still answer a later room-key request.
- [ ] Logs contain exception types or bounded summaries, never raw bearer-like values.

## Diagnostics

Collect evidence from the exact Hermes instance and runtime. Do not infer state from another agent's logs.

Useful safe evidence includes:

- exact Hermes version and installed Mautrix version;
- whether E2EE is `off`, `optional`, or `required`;
- Matrix `whoami` user/device identity, without the access token;
- joined encrypted-room count;
- whether the existing crypto store loaded successfully;
- peer device refresh result counts;
- actual prepared recipient count and identity fingerprints, with sensitive values redacted;
- whether the outbound session was persisted as shared;
- the event type and Megolm algorithm, never room keys or recovery keys.

Do not print `.env`, access tokens, recovery keys, raw authorization headers, or full exception strings from HTTP clients.

## Optional implementation bundle

- [`adapter.patch`](adapter.patch) — adapter implementation changes.
- [`tests.patch`](tests.patch) — regression tests and existing Matrix test updates.
- [`upstream-pr.md`](upstream-pr.md) — provenance and verification record.

Apply patches only after checking the target Hermes revision and Mautrix API compatibility. The patches are not a substitute for end-to-end acceptance in the target runtime.
