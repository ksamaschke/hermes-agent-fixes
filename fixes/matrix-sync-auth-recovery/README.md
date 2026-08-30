# Matrix sync authentication recovery

## Purpose

This bundle prevents Hermes' Matrix inbound sync loop from stopping permanently when a transient transport exception or human-readable response message happens to contain authentication-like text such as `401`, `403`, `unauthorized`, `forbidden`, or `unknown_token`.

It uses Hermes' supported user-plugin registration mechanism. It does **not** modify the Hermes checkout, rotate the Matrix access token, replace the Matrix device, or delete the E2EE crypto store.

## Symptom

A Hermes gateway can appear partly healthy while Matrix inbound delivery is dead:

- the gateway process remains running;
- outbound sends may later work;
- the same access token still passes Matrix `whoami`;
- no new inbound events are dispatched;
- the log contains `Matrix: permanent auth error ... — stopping sync`;
- restarting the gateway restores sync with the same token and device.

This split state occurs because the Matrix sync coroutine can return while the rest of the gateway remains alive.

## Root cause

The affected bundled adapter classifies exceptions by scanning `str(exc).lower()` for any occurrence of:

```text
401
403
unauthorized
forbidden
```

That is unsafe for connection wrappers and other free-form messages. Examples that are not proof of Matrix token invalidation include:

```text
500 Internal Server Error (request id req-4012ab)
connection timeout to peer ws-403-prod
```

Transient DNS, TLS, reverse-proxy, timeout, or connection failures can therefore enter the permanent-auth branch. Once `_sync_loop()` returns, the gateway has no confirmed mechanism that recreates the task, so inbound Matrix sync remains stopped until process replacement.

The important distinction is:

- **trigger:** a transient network, proxy, DNS, timeout, or connection failure;
- **persistent failure:** free-form exception text is misclassified as terminal authentication failure and the sync task exits.

A `401` in logs is not, by itself, evidence that a Matrix access token was revoked. Confirm token state with a redacted, read-only `/_matrix/client/v3/account/whoami` request.

## Correct behavior

The override retains the bundled adapter's Matrix behavior but changes the terminal classification boundary:

- a typed `mautrix.errors.MUnknownToken` is terminal;
- a structured `MatrixRequestError` whose `errcode` is exactly `M_UNKNOWN_TOKEN` is terminal;
- a returned sync error object whose machine-readable `errcode` is exactly `M_UNKNOWN_TOKEN` is terminal;
- cancellation exits normally;
- connection wrappers, generic exceptions, and human-readable response messages remain retryable regardless of incidental authentication-like text;
- non-success response objects that are not `M_UNKNOWN_TOKEN` retry after the existing five-second delay.

This favors a visible retry loop over a silent, permanently stopped inbound sync task when the error is ambiguous.

## Bundle contents

- [`override/platforms/matrix/`](override/platforms/matrix/) — explicitly enabled user platform plugin.
- [`tests/test_matrix_sync_auth_override.py`](tests/test_matrix_sync_auth_override.py) — focused regression and registration tests.
- [`verification.md`](verification.md) — compatibility, provenance, and honest verification status.

### How the override is selected

The bundled manifest's discovery key is `matrix-platform`. The user plugin is discovered separately as `platforms/matrix` and must be explicitly enabled. Its registration runs after the bundled platform registration and registers the same platform name, `matrix`; Hermes' plugin ownership ledger replaces the active factory and restores the bundled registration when the user plugin is removed. This is a platform-registry override, not a same-discovery-key manifest collision.

## Compatibility

The bundle was prepared against:

- Hermes Agent `v0.20.6 (2026.8.27)`;
- Hermes source revision `26350357d76e4508c8df9304a3374bdc5a6f6220`;
- Mautrix `0.21.1`;
- Python `3.11`.

It intentionally imports private bundled symbols (`MatrixAdapter` and `_build_adapter`) and reproduces the installed `_sync_loop` control flow around the classifier. Treat it as version-specific.

After every Hermes update:

1. compare the installed bundled `_sync_loop` with this override;
2. run plugin doctor;
3. rerun the focused tests with the updated Hermes virtual environment;
4. verify the override marker and a live Matrix sync before relying on it.

Do not install the bundle if another user plugin already owns `platforms/matrix` until the two overrides have been reconciled.

## Install

Set `HERMES_HOME` to the exact profile home used by the target gateway. The default profile usually uses `$HOME/.hermes`; other profiles have separate homes.

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export FIX_BUNDLE="/path/to/hermes-agent-fixes/fixes/matrix-sync-auth-recovery"
```

### 1. Check for an existing user override

```bash
test ! -e "$HERMES_HOME/plugins/platforms/matrix" || {
  printf '%s\n' 'A user Matrix plugin already exists; inspect and reconcile it first.'
  exit 1
}
```

### 2. Copy only the override package

```bash
mkdir -p "$HERMES_HOME/plugins/platforms"
cp -R "$FIX_BUNDLE/override/platforms/matrix" \
  "$HERMES_HOME/plugins/platforms/matrix"
```

Do not copy `.env`, session state, or Matrix crypto files into the plugin directory.

### 3. Validate and enable it

```bash
hermes plugins doctor "$HERMES_HOME/plugins/platforms/matrix" --ci
hermes plugins enable platforms/matrix --no-allow-tool-override
hermes plugins list
```

Expected discovery state:

```text
matrix-platform  enabled  ...  user
```

A platform adapter does not need permission to replace Hermes' built-in tools.

### 4. Run the focused regression

Run `hermes --version`, copy its reported **Install directory** into `HERMES_CHECKOUT`, and use that checkout's virtual environment. `HERMES_HOME` is profile state and is not the checkout path for named profiles.

```bash
hermes --version
export HERMES_CHECKOUT="/absolute/install/directory/reported/above"
cd "$HERMES_CHECKOUT"
"$HERMES_CHECKOUT/venv/bin/python" -m pytest \
  "$FIX_BUNDLE/tests/test_matrix_sync_auth_override.py" -q
```

### 5. Load the plugin

Configuration and tests are only **prepared** state. Replace the supervised gateway process through Hermes' supported service command:

```bash
hermes gateway status
hermes gateway restart
```

For a Linux system-level gateway service, preserve the service scope instead:

```bash
hermes gateway status --system
hermes gateway restart --system
```

If `hermes gateway status` says the service definition is stale and gives a different remediation command, follow that exact command so the service definition is refreshed as well as the process.

## Live verification

Do not call the installation successful from plugin doctor, tests, startup logs, or an HTTP `200` alone. Verify all applicable items:

- [ ] `hermes plugins list` reports the Matrix plugin as `enabled` and source `user`.
- [ ] The supervised gateway has a new process ID after activation.
- [ ] The log contains `matrix-sync-auth-type-aware-v2` from the user plugin module.
- [ ] Matrix authentication resolves the expected user and device without printing the token.
- [ ] Matrix initial sync completes and the gateway reports Matrix connected.
- [ ] The same process remains alive beyond at least one 45-second sync guard interval.
- [ ] The process retains active Matrix transport and logs no new terminal-auth/import failure.
- [ ] A fresh inbound message from another Matrix account is dispatched.
- [ ] For encrypted rooms, the recipient can decrypt the fresh reply.

The final two items require an external Matrix client. Do not synthesize or backfill them from startup output.

## Rollback

The bundled plugin key (`matrix-platform`) and user plugin key (`platforms/matrix`) are distinct. Do not merely rename the directory inside `$HERMES_HOME/plugins`; Hermes still discovers plugin manifests under that root. Also do not run `hermes plugins disable platforms/matrix` during this rollback: on the target Hermes version, that command leaves the displayed Matrix platform disabled after the user manifest is removed.

Move the user plugin completely outside the plugin discovery tree. The dormant `platforms/matrix` enable entry is harmless while no user manifest exists and makes restoring the backup explicit and reversible:

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export BACKUP_DIR="$HERMES_HOME/plugin-backups/matrix-sync-auth-type-aware-v2"

test ! -e "$BACKUP_DIR" || {
  printf '%s\n' "Backup already exists: $BACKUP_DIR"
  exit 1
}

mkdir -p "$HERMES_HOME/plugin-backups"
mv "$HERMES_HOME/plugins/platforms/matrix" "$BACKUP_DIR"
hermes plugins list
hermes gateway restart
```

For a Linux system-level gateway service, use `hermes gateway restart --system` for the final command.

Before restarting, confirm `hermes plugins list` reports `matrix-platform` from source `bundled` and no user Matrix plugin. Rollback does not require token rotation or crypto-store deletion.

## Security invariants

- Never commit or print Matrix access tokens, passwords, recovery keys, room keys, cookies, authorization headers, or private homeserver URLs.
- Preserve the existing Matrix device identity and crypto database.
- Do not weaken sender/room allowlists or E2EE mode to make the test pass.
- Do not run two Matrix clients with the same token/device concurrently.
- Do not grant built-in tool-override permission to this platform plugin.
- Keep retries bounded by the adapter's existing delay; do not add a busy loop.
- Treat a future incompatibility with bundled private symbols as a fail-closed plugin load error, not a reason to edit the live Hermes checkout blindly.
