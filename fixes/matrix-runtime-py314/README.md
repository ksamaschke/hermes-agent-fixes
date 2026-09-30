# Hermes on macOS: Matrix E2EE on the managed Python 3.14 runtime

Replaces the earlier Python 3.11 venv approach (a locally built `python-olm` inside `~/.hermes/hermes-agent/venv`).
Verified 2026-09-30 on Apple Silicon (macOS, Apple clang, CMake 4.4) with Hermes' managed
CPython 3.14.7 and the **stock, unmodified** Hermes launcher.

**Nothing in Hermes is patched.** Hermes updates only touch the managed environment; the Matrix
dependencies live in a user-owned directory and are loaded by a small user plugin.

## Why Matrix stopped working

Recent Hermes versions start the gateway from a managed Python 3.14 runtime that Hermes rebuilds on
update. Its `matrix` extra is gated to Linux in `pyproject.toml` (`[tool.hermes.extras-platforms]`
`matrix = "sys_platform == 'linux'"`), because `python-olm` has no macOS wheel and its bundled libolm
does not compile with current clang and CMake >= 4. The result on macOS:

```
Platform 'Matrix' requirements not met (pip install 'mautrix[encryption]')
No adapter available for matrix
```

The old 3.11 venv (`~/.hermes/hermes-agent/venv`) still had a locally built `python-olm`, so Matrix only
worked as long as Hermes ran from that venv. After an update and gateway restart it did not any more.

## Two build problems, both fixable

1. **clang:** `libolm/include/olm/list.hh` declares `T * const other_pos` and then increments it.
   `libolm-list-hh-const.patch` removes the `const` .
2. **CMake 4:** the vendored `CMakeLists.txt` uses an old `cmake_minimum_required`. Set
   `CMAKE_POLICY_VERSION_MINIMUM=3.5` for the build.

The built library (`_libolm.abi3.so`) links only `libc++` and `libSystem`. The wheel is tagged
`cp314-cp314-macosx_11_0_arm64` and only loads under CPython 3.14 on arm64.

## What is in this directory

| Path | Purpose |
| --- | --- |
| `wheels/python_olm-3.2.16-cp314-cp314-macosx_11_0_arm64.whl` | Prebuilt cp314 wheel (Apache-2.0, see `wheels/NOTICE`), checksum in `wheels/SHA256SUMS` |
| `libolm-list-hh-const.patch` | The one-line source patch |
| `plugin/matrix-runtime-py314/` | Hermes user plugin that appends the dependency directory to `sys.path` |
| `install.sh` | Installs the dependency directory and plugin; `--rebuild` builds the wheel from the PyPI sdist (checksum-verified) |

## Install

```bash
cd fixes/matrix-runtime-py314
./install.sh              # bundled wheel
./install.sh --rebuild    # or: build from source yourself
```

This creates `~/.hermes/vendor/matrix-py314/` (python-olm, mautrix 0.21.1, asyncpg, aiosqlite,
aiohttp-socks 0.11.0, python-socks 2.8.2, cffi, base58, pycryptodome, unpaddedbase64) and copies the plugin to
`~/.hermes/plugins/matrix-runtime-py314/`. Versions match Hermes' own pins for the `matrix` extra.

Enable the plugin in `~/.hermes/config.yaml`, **before** any plugin that imports mautrix at load time:

```yaml
plugins:
  enabled:
    - matrix-runtime-py314
    - matrix-store-guard   # optional
platforms:
  matrix:
    enabled: true
    extra:
      e2ee_mode: required
```

Restart the gateway with the stock LaunchAgent (`hermes gateway restart`). Expected log lines:

```
matrix-runtime-py314: Matrix E2EE dependencies exposed from ~/.hermes/vendor/matrix-py314
Matrix: E2EE enabled (store: ~/.hermes/platforms/matrix/store/crypto.db, ...)
Matrix: initial sync complete, joined N rooms
✓ matrix connected
```

## How the plugin stays upstream-compatible

- Appends to `sys.path` and never prepends: anything the managed environment provides (aiohttp, yarl,
  attrs, ...) wins, so the plugin cannot shadow upstream packages.
- Does nothing on any interpreter other than CPython 3.14, or when the directory is missing.
- Installs nothing, does not touch the crypto store, and does not register a platform. The bundled
  Matrix adapter is used as shipped.
- Hermes' own availability check (`pm.extras.available('matrix')`) turns true because the anchors
  (`mautrix`, `asyncpg`, `aiosqlite`, `markdown`, `aiohttp_socks`) become importable. An installed
  override beats the platform gate by design.

## Do not use these workarounds

- A patched launcher, a wrapper entry point, or a LaunchAgent pointing at the old 3.11 venv. Hermes
  regenerates the LaunchAgent (observed 2026-09-29 23:18 after a self-restart) and Matrix silently
  goes down again.
- Installing into the managed environment. Hermes rebuilds it on update.
- Re-registering the `matrix` platform from a plugin with a copied adapter. It goes stale against the
  bundled adapter and can report the platform as unavailable (seen with a `tools.lazy_deps` probe that
  no longer exists).

## Maintenance

- **Python minor upgrade** (e.g. 3.14 to 3.15): the compiled library and the cp314 binary wheels
  (asyncpg) no longer match. Run `./install.sh --rebuild` and adjust `_REQUIRED_PYTHON` in the plugin.
- **Hermes changes the `matrix` pins:** rerun `install.sh` with matching versions.
- **Hermes ships macOS support for Matrix upstream:** remove `matrix-runtime-py314` from
  `plugins.enabled` and delete `~/.hermes/vendor/`.
- **Check after any Hermes update:**
  ```bash
  grep 'matrix connected' ~/.hermes/logs/gateway.log | tail -1
  grep 'matrix-runtime-py314' ~/.hermes/logs/agent.log | tail -1
  ```

## Warnings that are not this problem

- `Failed to decrypt megolm event: no session with given ID`: room keys for messages sent before this
  device had them. Not a dependency problem.
- `No one-time keys nor device keys got when trying to share keys`: seen on every start; it did not
  block encrypted messages in testing.
- Standalone-gateway warning (`hermes gateway migrate --multiplex`): profile layout, unrelated.

## Security

The wheel is an unofficial binary. If you do not want to trust a binary from a repository, use
`./install.sh --rebuild`; the sdist is verified against a pinned SHA-256 before building. Do not commit
tokens, `.env` files or `crypto.db`.
