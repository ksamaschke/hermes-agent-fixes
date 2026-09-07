# matrix-token-rotator

Keeps the Matrix access tokens of Hermes agents fresh **without a human**.

## Problem

Agents created through `matrix-agent-manager` authenticate with MAS *personal
sessions* that expire after 30 days. The manager can regenerate a session, but
it only writes the result into a Kubernetes Secret — nothing delivers the new
token to the host that actually runs the gateway. So every 30 days somebody has
to copy a token into `.env` and restart, or the agent silently goes dead.

A second trap: the token in the Secret and the token on the host drift apart
(someone rotates in the dashboard, someone logs in by hand). Then a later
"rotation" moves the host onto a *different device id*, the adapter resets the
local Olm store, and a fresh identity is uploaded under a device that peers
already trust — the exact failure `matrix-store-guard`'s identity guard exists
to block.

## What it does

Runs once a day from launchd on the agent host. Per agent:

1. Reads the manager Secret (`matrix-agents/matrix-agent-<name>`) for the
   session id.
2. Asks MAS admin (via a `kubectl port-forward` to `matrix-mas-admin`, using the
   manager's own client credentials) for the session's expiry.
3. Refuses if the host's device id differs from the session's device — never
   moves a host to another device.
4. If the host token is invalid or expires in < 7 days: `POST
   …/personal-sessions/{id}/regenerate`, writes the new token into every `.env`
   the agent uses, patches the Secret (`access-token`, `generation`,
   `updated-at`) so the dashboard reflects the truth, and restarts the
   gateway(s) via `launchctl kickstart -k`.
5. Otherwise keeps the Secret equal to the host token (host is the consumer of
   record).
6. After a restart: `whoami` per agent to verify the device.

The device id and Olm store never change on rotation — only the bearer token.

## Install

```
cp matrix-token-rotator.py ~/.hermes/bin/
cp examples/<host>.json ~/.hermes/bin/matrix-token-rotator.json   # edit
cp launchd/ai.hermes.matrix-token-rotator.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.hermes.matrix-token-rotator.plist
```

Config: agent name → `.env` path(s) sharing that device's token, and the
launchd labels to restart:

```json
{"hex-work": {"env": ["~/.hermes/.env", "~/.hermes/profiles/reviewer/.env"],
              "labels": ["ai.hermes.gateway"]}}
```

Requires `kubectl` with a kubeconfig that may read/patch Secrets in
`matrix-agents`, read `matrix-admin`, and port-forward in `matrix`. Runs with
the Hermes venv Python (needs nothing beyond stdlib).

Manual run / force one agent: `python matrix-token-rotator.py --force=<name>`.
Logs: `~/.hermes/logs/matrix-token-rotator.log`.
