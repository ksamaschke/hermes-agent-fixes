# Matrix share verify

## Purpose

Makes sure a room key the agent believes it delivered was actually delivered.

An agent can encrypt every message correctly, record the session as shared, and
still leave the recipient with nothing but "Unable to decrypt" — indefinitely,
with no error anywhere on the agent side.

## The bug this closes

Mautrix marks an outbound Megolm session as shared even when it silently
skipped devices. From `mautrix/crypto/encrypt_megolm.py`:

```python
for user_id, devices in missing_sessions.items():
    for device_id, device in devices.items():
        result = await self._find_olm_sessions(...)
        ...
        # We don't care about missing keys at this point
...
self.log.info(f"Group session {session.id} for {room_id} successfully shared")
session.shared = True
```

A device with no usable Olm channel is dropped from the share, and the session
is still persisted with `shared=1`. Nothing retries, and nothing reports it.

The Hermes Matrix E2EE plugin already guards against this: it compares the
devices actually reached against the intended target set, discards the session
and fails closed when any were missed. That check is correct — but it sits
behind a cache short-circuit:

```python
if (session is not None and session.shared and not session.expired
        and cached_targets == (str(session.id), targets)):
    return
```

`_e2ee_room_targets` lives in process memory. After a restart it is empty, so
the first send per room fills it from whatever the database says — including a
session that was mis-shared *before* the restart. From that point the
verification is skipped for the entire lifetime of the process, and the broken
session is never re-examined.

Observed consequence: an agent whose peer could not read a single message for
hours, while the guard logged nothing at all, because it never ran.

## What the plugin does

Distrust-on-load. The first time a room is prepared in a new process, the
cached trust entry for that room is dropped, so the adapter's own fail-closed
verification runs against the restored session. If devices were missed, the
adapter discards the session and re-shares. One extra verification per room per
process; afterwards the normal cache behaviour applies.

Also included is the churn policy used to detect the failure from the other
side: a peer device that cannot open our Olm messages causes a fresh Olm
session on every attempt — dozens per hour instead of a handful per week. The
threshold logic is exposed and tested (`churn_exceeded`,
`count_recent_olm_sessions`) for diagnostics.

## Diagnosing the symptom by hand

Session churn per peer device, from the profile's crypto store:

```sql
select substr(created_at,1,10) day, count(*)
from crypto_olm_session group by 1 order by 1 desc;
```

Tens or hundreds in a single day means key delivery is failing at the Olm
layer, regardless of what `shared` says.

Forced recovery (what this plugin automates at startup):

```sql
delete from crypto_megolm_outbound_session where room_id='!room:server';
```

then restart. The next outbound message re-shares from scratch. Historical
events stay unreadable — their keys are gone — so the acceptance test is
whether a **newly sent** message is readable.

## Install

```yaml
plugins:
  enabled:
    - matrix-share-verify
```

## Verification status

Confirmed working. In the live gateway the plugin logs

```
matrix-share-verify: active (restored room key shares are re-verified once per room)
matrix-share-verify: revalidating restored room key share for !room:server before first send
```

and the revalidation was observed firing against a restored session during
normal operation, with no crypto warnings afterwards.

13 offline tests cover the churn policy, the per-room revalidation policy, the
install path, and store-probe robustness. The central regression test
reproduces the exact failure: a stale cache entry suppressing verification, and
the wrapper forcing it to run.

Not proven: that the plugin prevents every possible variant of a partial share.
It closes the restart-plus-stale-cache path that was observed; a share that
fails while the cache is already warm is caught by the adapter's own check, not
by this plugin.

## Design notes

Plugin-only. Patches nothing in the Hermes checkout and copies no upstream
file. The adapter class is reached through `type(adapter)` inside the
platform-handler factory — the adapter module is not yet imported when
`register()` runs, so importing it there raises `ModuleNotFoundError`. If the
host adapter has no `_ensure_encrypted_room_ready_impl`, the plugin logs and
does nothing.
