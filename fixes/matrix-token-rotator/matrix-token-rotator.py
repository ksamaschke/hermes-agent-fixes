#!/usr/bin/env python3
"""matrix-token-rotator — keeps Hermes agent tokens fresh without a human.

Runs from launchd once a day on the agent host. For every agent in AGENTS:

  1. Reads the manager Secret (matrix-agents/matrix-agent-<name>) -> session-id.
  2. Asks MAS for that personal session; if its access token expires within
     RENEW_BEFORE, calls POST /personal-sessions/{id}/regenerate — the exact
     call the matrix-agent-manager "rotate" button makes. Same device id, same
     Olm identity; only the token changes, so E2EE state is untouched.
  3. Writes the new token into the profile's .env (atomic replace, 0600),
     patches the manager Secret (access-token, generation+1, updated-at) so
     the Dashboard keeps showing the live state, and restarts the gateway.
  4. Verifies /account/whoami with the new token reports the expected device.

Also self-heals drift: if the .env token differs from the Secret token but
both are valid for the same device, the Secret wins only when the .env token
is rejected by the homeserver; otherwise the Secret is updated to the .env
token (the host is the consumer of record).

Secrets never reach stdout or the log; only presence, device ids, timestamps.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
KUBECTL = next((c for c in ("/opt/homebrew/bin/kubectl", "/usr/local/bin/kubectl") if os.path.exists(c)), "kubectl")
KUBECONFIG = next((str(c) for c in (HOME / ".kube" / "config-mac-k3s", HOME / ".kube" / "config-mac-k3s-cluster", HOME / ".kube" / "homelab", HOME / ".kube" / "config") if c.exists()), "")
HS = "https://matrix.home.samaschke.de"
MAS_NS, MAS_SVC, MAS_PORT = "matrix", "svc/matrix-mas-admin", 8081
MGR_NS, MGR_CLIENT_SECRET = "matrix-admin", "matrix-agent-manager-mas-client"
AGENT_NS = "matrix-agents"
RENEW_BEFORE = dt.timedelta(days=7)
TOKEN_TTL = 30 * 24 * 3600
GATEWAY_LABEL = "ai.hermes.gateway"

# Per-host map, agent name -> {"env": <.env path>, "labels": [launchd labels to restart]}
# from ~/.hermes/bin/matrix-token-rotator.json, e.g.
#   {"ponder-stibbons": {"env": "~/.hermes/.env", "labels": ["ai.hermes.gateway"]}}
CONFIG = HOME / ".hermes" / "bin" / "matrix-token-rotator.json"
_cfg = json.loads(CONFIG.read_text())
AGENTS = {k: [Path(e).expanduser() for e in (v["env"] if isinstance(v["env"], list) else [v["env"]])]
          for k, v in _cfg.items()}
LABELS = {k: v.get("labels", [GATEWAY_LABEL]) for k, v in _cfg.items()}

LOG = HOME / ".hermes" / "logs" / "matrix-token-rotator.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("rotator")


# ---------------------------------------------------------------- helpers
def kubectl(*args: str) -> str:
    env = dict(os.environ, KUBECONFIG=KUBECONFIG)
    return subprocess.check_output([KUBECTL, *args], env=env, text=True,
                                   stderr=subprocess.STDOUT)


def secret_data(ns: str, name: str) -> dict[str, str]:
    raw = json.loads(kubectl("-n", ns, "get", "secret", name, "-o", "json"))
    return {k: base64.b64decode(v).decode() for k, v in raw["data"].items()}


def patch_secret(ns: str, name: str, data: dict[str, str]) -> None:
    body = {"data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}}
    kubectl("-n", ns, "patch", "secret", name, "--type=merge", "-p", json.dumps(body))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PortForward:
    def __init__(self) -> None:
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "PortForward":
        env = dict(os.environ, KUBECONFIG=KUBECONFIG)
        self.proc = subprocess.Popen(
            [KUBECTL, "-n", MAS_NS, "port-forward", MAS_SVC, f"{self.port}:{MAS_PORT}"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.25)
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                return self
            except OSError:
                continue
        raise RuntimeError("MAS port-forward did not come up")

    def __exit__(self, *_: object) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def http(method: str, url: str, *, tok: str | None = None, body: dict | None = None,
         form: dict | None = None, basic: tuple[str, str] | None = None) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{basic[0]}:{basic[1]}".encode()).decode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
            return r.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, {"raw": payload.decode(errors="replace")[:200]}


def mas_admin_token(base: str) -> str:
    creds = secret_data(MGR_NS, MGR_CLIENT_SECRET)
    csec = creds["client-secret"]
    cm = json.loads(kubectl("-n", MGR_NS, "get", "cm", "matrix-agent-manager", "-o", "json"))
    cid = cm["data"]["AGENT_MANAGER_MAS_CLIENT_ID"]
    st, r = http("POST", f"{base}/oauth2/token", basic=(cid, csec),
                 form={"grant_type": "client_credentials", "scope": "urn:mas:admin"})
    if st != 200:
        raise RuntimeError(f"MAS client_credentials failed: {st}")
    return r["access_token"]


def whoami(tok: str) -> dict:
    st, r = http("GET", f"{HS}/_matrix/client/v3/account/whoami", tok=tok)
    return r if st == 200 else {}


def env_token(path: Path) -> str:
    m = re.search(r'^MATRIX_ACCESS_TOKEN=["\']?([^"\'\n]+)', path.read_text(), re.M)
    return m.group(1) if m else ""


def write_env_token(path: Path, tok: str) -> None:
    text = path.read_text()
    new, n = re.subn(r'^MATRIX_ACCESS_TOKEN=.*$', f"MATRIX_ACCESS_TOKEN={tok}", text, flags=re.M)
    if n != 1:
        raise RuntimeError(f"{path}: expected exactly one MATRIX_ACCESS_TOKEN line, found {n}")
    tmp = path.with_suffix(path.suffix + ".rotator-tmp")
    tmp.write_text(new)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def restart_gateway(labels: set[str]) -> None:
    uid = os.getuid()
    for label in sorted(labels):
        for dom in (f"user/{uid}", f"gui/{uid}"):
            r = subprocess.run(["launchctl", "kickstart", "-k", f"{dom}/{label}"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                log.info("%s restarted via %s", label, dom)
                break
        else:
            log.error("could not kickstart %s in user/ or gui/ domain", label)


# ---------------------------------------------------------------- main
def process_agent(name: str, env_paths: list[Path], base: str, admin: str, force: bool = False) -> bool:
    env_path = env_paths[0]
    """Returns True when a restart is required."""
    sec_name = f"matrix-agent-{name}"
    sec = secret_data(AGENT_NS, sec_name)
    sid = sec["session-id"]
    st, ps = http("GET", f"{base}/api/admin/v1/personal-sessions/{sid}", tok=admin)
    if st != 200:
        raise RuntimeError(f"{name}: session {sid} not found in MAS ({st}) — manager Secret stale")
    attrs = ps["data"]["attributes"]
    if attrs.get("revoked_at"):
        raise RuntimeError(f"{name}: session {sid} is revoked — needs re-issue, refusing to guess")
    expires = dt.datetime.fromisoformat(attrs["expires_at"].replace("Z", "+00:00")) \
        if attrs.get("expires_at") else None
    now = dt.datetime.now(dt.timezone.utc)
    host_tok = env_token(env_path)
    host_who = whoami(host_tok) if host_tok else {}
    log.info("%s: session=%s device=%s host_token_valid=%s expires=%s",
             name, sid, host_who.get("device_id"), bool(host_who),
             expires.isoformat() if expires else "?")

    session_dev = attrs["scope"].split("urn:matrix:client:device:")[-1].split()[0]
    if host_who and host_who.get("device_id") != session_dev:
        # Never move a host onto another device id: the local crypto store
        # belongs to the running device; switching would let the adapter
        # mint a new Olm identity under an existing device (peers go deaf).
        raise RuntimeError(f"{name}: host runs device {host_who.get('device_id')} but manager session "
                           f"{sid} is for {session_dev} — refusing; re-point the Secret to the host's session")
    need_rotate = force or (not host_who) or (expires is not None and expires - now < RENEW_BEFORE)
    if not need_rotate:
        # keep every sibling .env on the primary's token
        for ep in env_paths[1:]:
            if env_token(ep) != host_tok:
                write_env_token(ep, host_tok)
                log.info("%s: %s synced to primary token", name, ep)
        # keep Secret == host (host is the consumer of record)
        if sec.get("access-token") != host_tok:
            patch_secret(AGENT_NS, sec_name, {"access-token": host_tok,
                                              "updated-at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
            log.info("%s: Secret access-token synced to host", name)
        return False

    log.info("%s: rotating (host_valid=%s, remaining=%s)", name, bool(host_who),
             (expires - now) if expires else "?")
    st, r = http("POST", f"{base}/api/admin/v1/personal-sessions/{sid}/regenerate",
                 tok=admin, body={"expires_in": TOKEN_TTL})
    if st not in (200, 201):
        raise RuntimeError(f"{name}: regenerate failed ({st}): {r}")
    new_tok = r["data"]["attributes"].get("access_token")
    if not new_tok:
        raise RuntimeError(f"{name}: regenerate returned no access_token")
    who = whoami(new_tok)
    if not who or who.get("device_id") != sec.get("device-id", who.get("device_id")):
        raise RuntimeError(f"{name}: new token whoami mismatch: {who}")
    for ep in env_paths:
        write_env_token(ep, new_tok)
    gen = int(sec.get("generation", "1")) + 1
    patch_secret(AGENT_NS, sec_name, {
        "access-token": new_tok,
        "generation": str(gen),
        "updated-at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    log.info("%s: rotated -> device=%s generation=%d .env updated", name, who["device_id"], gen)
    return True


def main() -> int:
    force = set(a[len("--force="):] for a in sys.argv[1:] if a.startswith("--force="))
    restart = False
    labels: set[str] = set()
    failures = 0
    with PortForward() as pf:
        admin = mas_admin_token(pf.base)
        for name, env_paths in AGENTS.items():
            try:
                if process_agent(name, env_paths, pf.base, admin, force=name in force):
                    restart = True
                    labels.update(LABELS[name])
            except Exception as e:  # keep going for the other agent
                failures += 1
                log.error("%s: %s", name, e)
    if restart:
        restart_gateway(labels)
        time.sleep(20)
        for name, env_paths in AGENTS.items():
            who = whoami(env_token(env_paths[0]))
            log.info("%s: post-restart whoami device=%s", name, who.get("device_id"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
