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

## Final prepared gate

The final artifact produced:

```text
Focused plugin suite: 33 passed
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
004cea8bdffcd84509f9bf12602f02abf0932b65891b2b19466f0adaaa349e89  override/matrix-sync-auth-fix/__init__.py
33accf06888b7c586effa271a2c64f2120344a9ce9314e84f7851aa570400a0e  override/matrix-sync-auth-fix/patched_upstream.py
ff0756ce1aa58d4a451795c4c7cd2ebd7c3e5a8122d5a5743c317377c0546aea  override/matrix-sync-auth-fix/plugin.yaml
a2112d2083099f5fa57195ef0ac3ecdd2838644c011ca3da0f28cc1645d2b879  override/matrix-sync-auth-fix/tests/test_matrix_sync_auth_classification.py
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

The prepared artifact was activated through the supervised Hermes service path. The previous process did not complete graceful shutdown and was intentionally replaced through the authorized hard-restart path.

The replacement process then produced all of the following loaded evidence:

```text
combined marker: matrix-e2ee-recipient-enforced-v1+matrix-sync-auth-type-aware-v2
base revision: ab9866bc64df
E2EE: enabled with the existing device and protected crypto store
initial sync: completed
platform reconnect: successful after bounded retries
```

The Hermes checkout remained clean and the user plugin remained the single enabled Matrix owner.

This establishes **loaded and connected**, not recipient-visible E2EE success. A fresh external encrypted message is still required to prove:

- inbound decryption and room/session routing;
- agent completion;
- a positive recipient target count during Megolm preparation;
- an encrypted sent event; and
- a visibly decrypted reply on the recipient client.

Startup, tests, sender-side event IDs, or a healthy API endpoint cannot prove the final item.

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
