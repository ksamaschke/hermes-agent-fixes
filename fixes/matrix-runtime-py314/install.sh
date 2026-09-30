#!/usr/bin/env bash
# Install / rebuild the Matrix E2EE dependency directory for Hermes' managed
# Python 3.14 on macOS Apple Silicon. No Hermes code is modified.
#
#   ./install.sh            install from the bundled wheel (fast, offline for olm)
#   ./install.sh --rebuild  rebuild python-olm from the PyPI sdist with the patch
#
# See README.md
set -euo pipefail

HERMES_ROOT="${HERMES_ROOT:-$HOME/.hermes}"
VENDOR="${MATRIX_PY314_VENDOR_DIR:-$HERMES_ROOT/vendor/matrix-py314}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OLM_VERSION="3.2.16"
OLM_SDIST_SHA256="a1c47fce2505b7a16841e17694cbed4ed484519646ede96ee9e89545a49643c9"

# Hermes' managed interpreter (the exact one that runs the gateway).
PY="${HERMES_PY314:-$(ls -d "$HERMES_ROOT"/tools/python-3.14*-darwin-arm64/bin/python3 2>/dev/null | tail -1)}"
[ -x "$PY" ] || { echo "managed Python 3.14 not found under $HERMES_ROOT/tools (set HERMES_PY314)"; exit 1; }
"$PY" -c 'import sys; assert sys.version_info[:2]==(3,14), sys.version' \
  || { echo "interpreter is not Python 3.14: $PY"; exit 1; }

WHEELS="$HERMES_ROOT/vendor/wheels-py314"
mkdir -p "$WHEELS" "$VENDOR"
WHEEL="$WHEELS/python_olm-$OLM_VERSION-cp314-cp314-macosx_11_0_arm64.whl"

if [ "${1:-}" = "--rebuild" ] || [ ! -f "$HERE/wheels/$(basename "$WHEEL")" ]; then
  echo ">> building python-olm $OLM_VERSION for $("$PY" -V)"
  T="$(mktemp -d /tmp/olmbuild.XXXXXX)"
  URL="$(curl -fsS "https://pypi.org/pypi/python-olm/$OLM_VERSION/json" \
    | python3 -c "import sys,json;print([u['url'] for u in json.load(sys.stdin)['urls'] if u['packagetype']=='sdist'][0])")"
  curl -fsSL "$URL" -o "$T/olm.tar.gz"
  echo "$OLM_SDIST_SHA256  $T/olm.tar.gz" | shasum -a 256 -c -
  tar xzf "$T/olm.tar.gz" -C "$T"
  ( cd "$T/python-olm-$OLM_VERSION" && patch -p1 < "$HERE/libolm-list-hh-const.patch" )
  "$PY" -m venv "$T/bv"
  "$T/bv/bin/pip" install -q setuptools wheel cffi
  # CMake >= 4 rejects the vendored libolm's old cmake_minimum_required.
  ( cd "$T/python-olm-$OLM_VERSION" && CMAKE_POLICY_VERSION_MINIMUM=3.5 \
      "$T/bv/bin/pip" wheel . --no-deps --no-build-isolation -w "$WHEELS" )
else
  ( cd "$HERE/wheels" && shasum -a 256 -c SHA256SUMS )
  cp "$HERE/wheels/$(basename "$WHEEL")" "$WHEELS/"
fi

echo ">> installing dependency directory $VENDOR"
"$PY" -m pip install --target "$VENDOR" --upgrade --no-deps --only-binary=:all: \
  --find-links "$WHEELS" \
  "python-olm==$OLM_VERSION" 'mautrix==0.21.1' 'asyncpg==0.31.0' 'aiosqlite==0.22.1' \
  'aiohttp-socks==0.11.0' 'python-socks==2.8.2' unpaddedbase64 'base58==2.1.1' 'pycryptodome==3.23.0' cffi pycparser

echo ">> installing loader plugin"
mkdir -p "$HERMES_ROOT/plugins"
cp -R "$HERE/plugin/matrix-runtime-py314" "$HERMES_ROOT/plugins/"

echo ">> verifying"
# attrs/aiohttp/yarl come from Hermes' managed environment at runtime; include it here if present.
MANAGED="$(ls -d "$HERMES_ROOT"/installs/*/environments/*/venv/lib/python3.14/site-packages 2>/dev/null | tail -1 || true)"
"$PY" -I -c "
import sys
sys.path[:0] = [p for p in ['$MANAGED'] if p]
sys.path.append('$VENDOR')
import olm; a = olm.Account(); print('olm OK', sorted(a.identity_keys))
from mautrix.crypto import OlmMachine; import asyncpg, aiosqlite, aiohttp_socks; print('mautrix E2EE stack OK')"
echo
echo "Next: add 'matrix-runtime-py314' to plugins.enabled in $HERMES_ROOT/config.yaml"
echo "      (before matrix-store-guard, if you use it) and restart the gateway."
