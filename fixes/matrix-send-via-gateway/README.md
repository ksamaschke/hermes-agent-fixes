# matrix-send-via-gateway

Send Matrix messages from scripts **through the running gateway** — never
from a second process.

## The bug this removes

`hermes send --platform matrix` (or any script that instantiates its own
mautrix client) opens a second E2EE client on the crypto store the gateway is
already using. The newcomer syncs, ratchets the Olm sessions to every peer
device and writes them back; the gateway keeps its pre-ratchet state in
memory. Every room key the gateway sends from then on is unreadable for the
peers: **"Unable to decrypt"** — for every subsequent message, until the next
restart, and again after the next `hermes send`.

Symptom pattern in the store: all Olm sessions to the peer show the same
`last_encrypted` timestamp = the moment the foreign process ran, followed by a
fresh outbound Megolm session that nobody can open.

One Olm account, one process. Full stop.

## What it does

Registers a platform handler on the live Matrix adapter that opens

    <profile home>/platforms/matrix/send.sock

(one socket per profile, also under `multiplex_profiles`). One JSON line in,
one JSON line out; the message goes through the adapter's own `send()` —
same Megolm session, same Olm ratchets, same path as an agent reply.

`send.py` is the drop-in replacement for `hermes send`:

    send.py --home ~/.hermes/profiles/cheery --room '!room:server' --text 'hi'
    some-script | send.py --home ~/.hermes --room '!room:server'

Exit 0 + event id on stdout; error on stderr otherwise. Never opens a Matrix
client of its own.

## Install

    cp -r fixes/matrix-send-via-gateway/plugin/matrix-send-via-gateway ~/.hermes/plugins/
    # config.yaml: plugins.enabled += matrix-send-via-gateway

Multiplexed secondary profiles discover plugins from **their own**
`<profile>/plugins/` only — symlink the shared plugin dirs there
(`platforms`, `matrix-store-guard`, …, `matrix-send-via-gateway`) or the
secondary profile runs with no platform handlers at all.

Then replace every `hermes_cli.main send` in cron/watchdog scripts with
`send.py` (see `publish_cron_output.example.sh`).
