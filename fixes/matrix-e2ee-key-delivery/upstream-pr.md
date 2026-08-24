# Provenance and verification

## Source

The optional patch bundle was extracted from the verified Hermes worktree used to diagnose Matrix room-key delivery after reconnects.

- Initial source commit: `1cad9f1fabe36687fb6a7522fe93059ca09dcb63`
- Final bundle source commit: `b5df40697f`
- Integrated fork commit: `9bcc57c2399c27bb64d4196e8677d44b4c1d389d`
- Integrated Forgejo follow-up commit: `aa0ab3a96d9456514b0540e2022d552af7a05586`
- Target code area: `plugins/platforms/matrix/adapter.py`
- Mautrix API reviewed: `0.21.1`

The implementation was reviewed independently with an adversarial focus on:

- stale peer-device and session caches;
- zero and partial to-device recipients;
- optional/plaintext fallback;
- media-before-upload ordering;
- lifecycle races and cancellation;
- room-key-request preservation;
- raw exception, URL, and credential leakage.

Final bounded review verdict: **SHIP**.

## Local verification

```text
Matrix-focused suite: 249 passed, 2 skipped, 1 xfailed
Ruff: passed
Python compilation: passed
git diff --check: passed
```

The repository-wide test run was attempted but could not complete in the source environment: one run failed before collection because the runner was not attached to a terminal, and a PTY retry exceeded the execution ceiling. No full-suite pass is claimed.

## Applying the optional bundle

1. Check out the Hermes revision the bundle targets.
2. Inspect the adapter and Mautrix versions.
3. Apply `adapter.patch` and `tests.patch` in a disposable worktree.
4. Run the targeted Matrix tests with the target runtime's Python environment.
5. Run lint, compilation, and the repository's canonical test wrapper where the environment supports it.
6. Perform a live acceptance test in the exact target Hermes instance.
7. Do not modify the live crypto store or rotate the Matrix device as part of patch application.

No credentials, tokens, recovery keys, or live routes are required by this bundle.
