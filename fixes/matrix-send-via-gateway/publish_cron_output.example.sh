#!/bin/bash
# Generic publisher: run an inner cron script, ship its output to the progress
# room instead of the origin chat.
#
# Why this wrapper exists: the scheduler's own delivery to an explicitly
# addressed ENCRYPTED matrix room fails (_MatrixStateInspectionError /
# "Timeout context manager should be used inside a task"). the text is shipped
# from here through the running gateway's send socket (matrix-send-via-gateway).
#
# Contract preserved: if the inner script is silent, nothing is sent anywhere.
# Added on top: consecutive identical messages are suppressed, so a watchdog
# repeating "pipeline finished" every 10 minutes does not spam the room.
#
# Usage: publish_cron_output.sh <inner-script-name> <dedupe-key>
set -uo pipefail

INNER="${1:?inner script name required}"
KEY="${2:?dedupe key required}"

PROFILE_DIR="$HOME/.hermes/profiles/cheery"
PY="$HOME/.hermes/hermes-agent/venv/bin/python"
ROOM="matrix:!DXnfXYTDbvGgUnnMWV:samaschke.de"
STATE="$PROFILE_DIR/cron/laststate_${KEY}.txt"

BODY="$(mktemp -t cronpub)"
trap 'rm -f "$BODY"' EXIT

if ! bash "$PROFILE_DIR/scripts/$INNER" > "$BODY" 2>&1; then
    echo "Inneres Skript '$INNER' fehlgeschlagen:"
    head -20 "$BODY"
    exit 1
fi

# Silent inner script -> nothing to report, stay quiet.
if [ ! -s "$BODY" ]; then
    exit 0
fi

# Suppress repeats of an unchanged message.
mkdir -p "$(dirname "$STATE")"
NEW_SUM="$(cksum < "$BODY" | awk '{print $1, $2}')"
if [ -f "$STATE" ] && [ "$(cat "$STATE")" = "$NEW_SUM" ]; then
    exit 0
fi

# Through the RUNNING gateway (one Olm account = one process). `hermes send`
# spawned a second E2EE client on the same crypto store and desynchronised the
# Olm ratchets -> every later room key undecryptable for peers.
if ! out="$("$PY" "$HOME/.hermes/plugins/matrix-send-via-gateway/send.py" --home "$PROFILE_DIR" --room "${ROOM#matrix:}" --text-file "$BODY" 2>&1)"; then
    echo "Zustellung in den Fortschrittsraum fehlgeschlagen ($INNER):"
    echo "$out" | tail -5
    exit 1
fi

echo "$NEW_SUM" > "$STATE"
exit 0
