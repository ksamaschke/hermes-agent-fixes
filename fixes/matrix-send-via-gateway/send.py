#!/usr/bin/env python3
"""Send a Matrix message through the running gateway (drop-in for `hermes send`).

    send.py --room '!abc:server' --text 'hello'      # or --text-file / stdin
Exit 0 on success; error on stderr, exit 1 otherwise. Never opens a Matrix
client of its own — see __init__.py for why that matters.
"""
import argparse, json, os, socket, sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--room", required=True)
ap.add_argument("--text")
ap.add_argument("--text-file")
ap.add_argument("--home", default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
a = ap.parse_args()
text = a.text if a.text is not None else (open(a.text_file).read() if a.text_file else sys.stdin.read())
sock = Path(a.home) / "platforms" / "matrix" / "send.sock"
if not sock.exists():
    print(f"gateway send socket missing: {sock} (gateway not running / plugin not loaded)", file=sys.stderr); sys.exit(1)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(60); s.connect(str(sock))
s.sendall((json.dumps({"room_id": a.room, "text": text}) + "\n").encode())
buf = b""
while not buf.endswith(b"\n"):
    chunk = s.recv(65536)
    if not chunk: break
    buf += chunk
r = json.loads(buf or b"{}")
if r.get("ok"):
    print(r.get("event_id")); sys.exit(0)
print("send failed:", r.get("error"), file=sys.stderr); sys.exit(1)
