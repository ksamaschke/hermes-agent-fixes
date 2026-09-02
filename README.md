# Hermes Agent Fixes

Reusable explanations, acceptance criteria, and optional patches for Hermes Agent issues that affect more than one instance.

This is **not** the upstream fork. The regularly synchronized source fork is [`ksamaschke/hermes-agent`](https://github.com/ksamaschke/hermes-agent). This repository is the explanation and fix-pack layer that other Hermes instances can consume selectively.

## Contents

- [`fixes/matrix-e2ee-key-delivery/`](fixes/matrix-e2ee-key-delivery/) — explanation and optional patch for Matrix E2EE room-key delivery after reconnects.
- [`fixes/matrix-sync-auth-recovery/`](fixes/matrix-sync-auth-recovery/) — combined user plugin for recipient-verified Matrix E2EE key delivery and structured sync-auth recovery.

Each fix directory should contain:

- an explanation/runbook first;
- explicit acceptance and security invariants;
- an optional patch or change bundle;
- regression tests or test patches where practical;
- provenance and honest verification status.

## Safety rules

- No access tokens, recovery keys, passwords, cookies, private keys, or live connection strings belong here.
- No live Hermes, Matrix, Synapse, or MAS runtime is changed by this repository.
- Do not delete an existing crypto store or rotate a device identity as a troubleshooting shortcut.
- Treat patches as version-specific: inspect the target Hermes and Mautrix versions before applying them.
