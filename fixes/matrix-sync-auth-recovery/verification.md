# Provenance and verification

## Source and compatibility

The override was extracted from a live diagnosis of Hermes Matrix sync recovery and then converted into a portable, explicitly enabled user-plugin bundle.

- Hermes Agent: `v0.20.6 (2026.8.27)`
- Hermes source revision: `26350357d76e4508c8df9304a3374bdc5a6f6220`
- Mautrix: `0.21.1`
- Python: `3.11`
- Target code area: `plugins/platforms/matrix/adapter.py::_sync_loop`
- Bundled plugin discovery key: `matrix-platform`
- User plugin discovery key: `platforms/matrix`
- Replaced platform registry name: `matrix`

No credential, account ID, room ID, deployment-specific homeserver hostname or IP address, recovery material, or crypto-store content is included in this repository. The manifest retains the public default example `https://matrix.org`.

## Root-cause evidence

The investigation established these independent facts:

1. Current DNS and Matrix API reachability were healthy during verification.
2. The existing access token returned `200` from Matrix `whoami` and identified the expected account/device.
3. Historical gateway logs showed transient timeout/DNS/connection errors before one `MatrixConnectionError` entered the permanent-auth branch.
4. The same token/device authenticated and synchronized successfully after process replacement.
5. The bundled `_sync_loop` used unanchored free-form substring checks for `401`, `403`, `unauthorized`, and `forbidden`.
6. The sync coroutine returned on that classification, while other gateway facilities could remain alive.
7. A focused harness reproduced the false stop with unrelated `401`/`403` digits in `MatrixConnectionError` messages.

The exact historical substring inside the one classified connection exception was not retained in the sanitized log. No claim is made about which of the four strings matched. The persistent failure mechanism is nevertheless deterministic and reproduced; a genuinely revoked token is contradicted by successful `whoami` and later synchronization with the same credentials.

## Test-first evidence

Before the override existed, the regression harness exercised the bundled adapter and failed both transport cases:

```text
2 failed
attempts: expected 2, observed 1
logged: Matrix: permanent auth error ... — stopping sync
```

After installing the override, the focused suite passed:

```text
4 passed in 0.12s
```

The portable repository suite expands coverage to include:

- transport errors containing unrelated `401` and `403` digits;
- free-form `unauthorized` and `forbidden` text;
- typed `MUnknownToken` terminal behavior;
- structured sync-response `M_UNKNOWN_TOKEN` terminal behavior;
- response messages containing `unknown_token` with absent or conflicting errcodes remaining retryable after the existing five-second delay;
- direct cancellation without retry delay;
- override factory registration and restoration of the upstream module global on success and failure;
- isolated CLI discovery, explicit enablement, and rollback to the bundled platform.

The bundle gate produced:

```text
Portable focused suite: 12 passed in 1.14s
Plugin doctor: passed discovery, manifest parsing, import, and registration
Isolated install/discovery: user Matrix plugin resolved as enabled
Isolated rollback: bundled Matrix plugin restored as enabled, no user plugin discovered
Python compilation: passed
git diff --check: passed
```

## Initial v1 prepared verification

The live-tested user plugin produced:

```text
Plugin Doctor: OK
runtime discovery, manifest parsing, import, and registration passed
matrix-platform: enabled, source user
focused regression: 4 passed
Hermes checkout: no local source changes
```

## Initial v1 loaded verification

After the supported supervised-service refresh:

- the gateway process was replaced;
- the service definition matched the installed Hermes runtime;
- the log identified the user-plugin module and marker `matrix-sync-auth-type-aware-v1`;
- Matrix initial sync completed across the joined rooms;
- the gateway reported Matrix connected.

That live process loaded the initial `1.0.1` / `matrix-sync-auth-type-aware-v1` variant. Review of the reusable bundle then found that human-readable response-message text could still be misclassified. The final `1.0.2` / `matrix-sync-auth-type-aware-v2` bundle removes that fallback and is covered by the 12-case portable suite. It was not reloaded into the live gateway during this repository publication workflow, so no final-v2 live activation is claimed here.

## Live verification

For the initial v1 deployment, more than two minutes after connection—longer than two 45-second sync guards—the same gateway child process remained alive with an established TLS connection to the Matrix homeserver. A fresh redacted `whoami` still returned `200`, and no post-load permanent-auth, sync, dependency, import, or adapter-creation error was present.

A fresh external encrypted message/reply was **not** generated during this verification. Recipient-visible decryption and full inbound-agent-outbound behavior therefore remain deployment acceptance items, not claimed evidence.

## Review focus

Review this bundle specifically for:

- whether every terminal path is backed by structured `M_UNKNOWN_TOKEN` evidence;
- accidental restoration of free-form auth substring matching;
- drift from the target bundled `_sync_loop` control flow;
- cancellation and retry behavior;
- registration factory restoration;
- secrets, live identifiers, or unsafe crypto-store advice;
- compatibility claims that exceed the stated Hermes/Mautrix versions.
