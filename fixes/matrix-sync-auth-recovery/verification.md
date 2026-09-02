# Provenance and verification

## Source and compatibility

This bundle combines two previously independent Matrix corrections into one user-owned platform registration.

- Hermes Agent: `0.21.0`
- Hermes source revision: `ab9866bc64df48281a2d929dfb1dfd1001973d24`
- Mautrix: `0.21.1`
- Python: `3.11`
- User plugin key: `matrix-sync-auth-fix`
- Replaced platform registry name: `matrix`
- E2EE source bundle: [`../matrix-e2ee-key-delivery/`](../matrix-e2ee-key-delivery/)

No credential, account ID, room ID, deployment-specific homeserver hostname or IP address, recovery material, or crypto-store content is included in this repository.

## Root-cause evidence

The investigation established three independent facts:

1. The bundled sync loop can terminate on free-form authentication-like text even when no structured `M_UNKNOWN_TOKEN` signal exists.
2. A Matrix sender can emit a valid encrypted event while no current peer device has verifiably received the corresponding outbound Megolm room key.
3. Two user plugins that both register `matrix` do not compose: the later registration replaces the earlier factory. The process can therefore retain only one correction even while both plugin directories exist.

The durable fix is one plugin that loads the recipient-enforcing adapter and then applies the structured sync-auth subclass to that exact base class.

## Test-first evidence

### E2EE adapter patch

The E2EE regression patch was applied first to a disposable worktree at the pinned Hermes revision.

RED, before the adapter implementation patch:

```text
ImportError: cannot import name '_build_hermes_olm_machine'
3 collection errors
RED_EXIT_STATUS=2
```

GREEN, after applying the adapter patch:

```text
139 passed
GREEN_EXIT_STATUS=0
```

This suite covers persisted outbound sessions, device refresh, zero and partial recipients, retry behavior, encrypted state failure, media ordering, and room-key request preservation.

### Combined plugin wiring

The initial combined wiring suite exposed four loader/closure failures before the wrapper was corrected:

```text
4 failed, 22 passed
```

After wiring the pinned adapter through the existing plugin owner:

```text
26 passed
```

### Structured sync-auth v2 correction

A follow-up review found two remaining free-form decision paths: returned response message text and bare HTTP status. Four tests were added first.

RED:

```text
4 failed, 28 passed
```

The failures proved that incidental `unknown_token` text and untyped `401`/`403` values could still stop sync.

GREEN after restricting terminal behavior to typed or structured `M_UNKNOWN_TOKEN`:

```text
32 passed
```

### Max-message-length drift guard

A pre-push review found that the registration mirrored `DEFAULT_MAX_MESSAGE_LENGTH` while only claiming to verify it. A fail-closed regression was added first.

RED:

```text
1 failed: DID NOT RAISE RuntimeError
```

GREEN after checking the sibling-pinned constant before adapter construction:

```text
1 passed
33 passed in the full focused suite
```

### Connect-readiness lifecycle correction

The live reconnect investigation found that initial E2EE key sharing and the
encrypted-room reconciliation pass were awaited before the Matrix adapter
returned from `connect()`. A slow or temporarily empty key response could
therefore exhaust the gateway's platform-connect budget even after initial
sync had completed. The corrected adapter schedules both operations in one
tracked task; the existing per-send recipient-verification guard remains
unchanged and fail-closed.

The regression tests cover task ownership, non-blocking readiness, key-share
ordering, reconciliation ordering, and cleanup:

```text
35 passed in 0.31s
```

## Final prepared gate

The final artifact produced:

```text
Focused plugin suite: 35 passed
Plugin Doctor: runtime discovery, manifest parsing, import, and registration passed
Python compilation: passed
Credential-literal scan: 0 token, 0 bearer, 0 private-key matches
Hermes checkout worktree diff: clean
Hermes checkout index diff: clean
Crypto backup SQLite integrity: ok
Live and backup crypto-store modes: 0600
```

The E2EE adapter and tests patches both passed `git apply --check` against the pinned Hermes revision before the disposable worktree was created.

## Final artifact hashes

```text
d7aa8314442ed57ee90afd189bd5267b41433e46ea003f034b6f7c84c1f1e12d  override/matrix-sync-auth-fix/__init__.py
6f8e135b1b471c3734f65f5842eee8a744c62a446467778f98c33936368e5eda  override/matrix-sync-auth-fix/patched_upstream.py
ff0756ce1aa58d4a451795c4c7cd2ebd7c3e5a8122d5a5743c317377c0546aea  override/matrix-sync-auth-fix/plugin.yaml
e20371e726b9fe16a2700b4a507c501f43f305d1c26940421d76aa8bbc087c1e  override/matrix-sync-auth-fix/tests/test_matrix_sync_auth_classification.py
```

## Isolated discovery and rollback

A temporary, isolated Hermes home confirmed the exact `0.21.0` lifecycle:

```text
enabled_user=[('matrix-platform', 'not enabled', 'bundled'),
              ('matrix-sync-auth-fix', 'enabled', 'user')]
after_move=[('matrix-platform', 'not enabled', 'bundled')]
after_bundled_enable=[('matrix-platform', 'enabled', 'bundled')]
```

This is why rollback documentation explicitly enables `matrix-platform` after moving the user plugin outside discovery.

## Activation evidence

The prepared artifact was activated through the supervised Hermes service path. The previous process did not complete graceful shutdown and was intentionally replaced through the authorized hard-restart path. The activation also exercised the bounded path for a restart with an interrupted in-flight turn; no crypto store or device identity was reset.

The replacement process then produced all of the following loaded evidence:

```text
combined marker: matrix-e2ee-recipient-enforced-v1+matrix-sync-auth-type-aware-v2
base revision: ab9866bc64df
E2EE: enabled with the existing device and protected crypto store
initial sync: completed
platform reconnect: successful after bounded retries
```

The Hermes checkout remained clean and the user plugin remained the single enabled Matrix owner.

This establishes **loaded and connected**. The post-fix startup returned from
the Matrix connect path after initial sync instead of waiting for E2EE
reconciliation to finish.

## External live evidence

A fresh probe supplied from an external Matrix client completed the full encrypted path:

- the gateway decrypted and routed the exact probe in an authoritatively encrypted Megolm room;
- the current joined-peer device set was non-empty, every eligible target had an identity key, and no eligible target was deleted or blacklisted;
- the guarded text-send path completed encrypted-room readiness before the event was emitted;
- the gateway produced the agent response and logged the following sent event; and
- the external recipient explicitly confirmed that the reply was visible and decryptable.

No private room, user, device, homeserver, host, event, or session identifier is included in this evidence. The final recipient-visible result is based on the external client confirmation, not inferred from startup, tests, sender-side event IDs, or an HTTP status.

The later lifecycle correction did not weaken the send guard. Its sender-side
activation observation showed a fresh inbound Matrix event followed by a sent
response after the replacement gateway reached connected state. This is
transport evidence only; a new recipient-visible decrypt confirmation must
still be obtained from an independent Matrix client before claiming a new
external E2EE round trip for this revision.

## Review focus

Review this bundle specifically for:

- any return of free-form authentication substring matching;
- terminal handling without typed or structured `M_UNKNOWN_TOKEN` evidence;
- adapter provenance drift from the pinned Hermes revision;
- zero or partial recipient sends that do not fail closed;
- media upload before encrypted-room readiness;
- duplicate Matrix platform registration;
- cancellation and bounded retry behavior;
- secrets, live identifiers, or unsafe crypto-store guidance; and
- compatibility claims beyond the stated Hermes/Mautrix versions.
