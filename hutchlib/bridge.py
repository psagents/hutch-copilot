"""The one implementation of the hutch bridges.

Replaces six verbatim copies of the same SSH+socket+JSON blob that were
embedded in markdown (``SKILL.md`` x3, ``are-we-ready/SKILL.md`` x2,
``bridge-to-cds/SKILL.md`` x1) as Python inside nested-quoted bash inside a
fenced code block. Two of those copies hardcoded
``/sdf/home/f/fpoitevi/.claude/skills/...``, so the bridge setup and both
readiness checks were broken for every user except the original author.

Three transports, matching the three LCLS environments:

  ipython  -> hutch-python session on a DAQ machine (oc_bridge.py on :9999)
  psdev    -> read /cds files; Python 3.9 stdlib only, no CA access
  controls -> hutch control machine; full EPICS CA

The remote client is passed to ssh as a single shell-quoted argv element, so
there is no nested-quoting hazard at all -- the reason the markdown version was
fragile.

Everything sent to a live session goes through ``safety.require_confirmation``
first.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

from . import paths, safety

#: SSH jump host for both /cds access and DAQ machines.
JUMP_HOST = "psdev"

#: Port oc_bridge.py listens on inside the hutch-python session.
BRIDGE_PORT = 9999

#: Timeout classes, in seconds. Named rather than magic numbers at call sites.
T_QUERY = 10
T_DEVICE = 30
T_SCAN = 300

# Client that runs *on* the DAQ machine and talks to the local listener.
# TCP forwarding (ssh -L) is disabled on DAQ machines, so the process must run
# there and connect to localhost itself.
_REMOTE_CLIENT = r"""
import socket, json, sys
payload = sys.stdin.buffer.read()
s = socket.socket()
s.settimeout(TIMEOUT)
s.connect(("localhost", PORT))
s.sendall(payload)
s.shutdown(socket.SHUT_WR)
buf = b""
while True:
    chunk = s.recv(65536)
    if not chunk:
        break
    buf += chunk
sys.stdout.write(buf.decode("utf-8", "replace"))
"""


class BridgeError(RuntimeError):
    pass


class BridgeUnavailable(BridgeError):
    """The bridge could not be reached.

    Callers must NOT fall back to a hardcoded default on this error. Report it
    and either degrade to explicitly-labelled stale state or ask the operator.
    Silent fallback to a literal is the bug class this refactor exists to kill.
    """


def _ssh(
    host: str,
    remote_cmd: str,
    *,
    stdin: bytes | None = None,
    timeout: int = T_QUERY,
    jump: str | None = JUMP_HOST,
) -> str:
    argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}"]
    if jump and host != jump:
        argv += ["-J", jump]
    argv += [host, remote_cmd]
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeUnavailable(f"ssh to {host} timed out after {timeout + 30}s") from exc
    except FileNotFoundError as exc:  # pragma: no cover - ssh always present at LCLS
        raise BridgeUnavailable("ssh not found on PATH") from exc

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise BridgeUnavailable(f"ssh to {host} failed (rc={proc.returncode}): {err}")
    return proc.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# IPython bridge (live hutch-python session)
# --------------------------------------------------------------------------
def daq_host(hutch: str) -> str:
    """DAQ machine for a hutch, e.g. mfx -> mfx-daq.

    Resolve from the hutch-python conf.yml when it matters -- see
    ``resolvers.daq``. This convention is only the fallback spelling.
    """
    return f"{hutch.strip().lower()}-daq"


def exec_code(
    code: str,
    *,
    hutch: str,
    confirm: str | None = None,
    timeout: int = T_QUERY,
    host: str | None = None,
) -> dict:
    """Run Python in the live hutch-python session.

    ``confirm`` is the action class a human has approved (see safety.py).
    Read-only snippets need no confirmation; anything else is refused without
    a matching class.
    """
    verdict = safety.require_confirmation(code, confirm)

    host = host or daq_host(hutch)
    client = (
        _REMOTE_CLIENT.replace("TIMEOUT", str(timeout + 20)).replace("PORT", str(BRIDGE_PORT))
    )
    remote_cmd = "python3 -c " + shlex.quote(client)
    raw = _ssh(
        host,
        remote_cmd,
        stdin=json.dumps({"code": code}).encode(),
        timeout=timeout,
    )
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError:
        raise BridgeError(
            f"bridge returned non-JSON -- is the listener running?\n"
            f"  start it in the hutch-python session with:\n"
            f"    exec(open('/tmp/oc_bridge.py').read())\n"
            f"  got: {raw[:300]!r}"
        ) from None

    return {
        "ok": resp.get("status") == "ok",
        "output": resp.get("output", ""),
        "error": resp.get("error"),
        "safety": verdict,
        "host": host,
    }


def run_script(
    path: str,
    *,
    hutch: str,
    call: str | None = None,
    confirm: str | None = None,
    timeout: int = T_SCAN,
) -> dict:
    """Ship a local script into the live session and optionally call a function.

    Replaces the ``SCRIPT=/sdf/home/f/fpoitevi/...`` incantation: the path is
    resolved relative to this skill's own install directory.
    """
    src = paths.skill_root() / path if not path.startswith("/") else None
    text = (src or __import__("pathlib").Path(path)).read_text()
    if call:
        text = f"{text}\n{call}\n"
    return exec_code(text, hutch=hutch, confirm=confirm, timeout=timeout)


def install_listener(hutch: str, host: str | None = None) -> str:
    """Copy oc_bridge.py to /tmp on the DAQ machine. Operator still starts it."""
    host = host or daq_host(hutch)
    src = paths.script("scripts", "oc_bridge.py")
    _ssh(host, "cat > /tmp/oc_bridge.py", stdin=src.read_bytes(), timeout=T_QUERY)
    return (
        f"installed {src.name} on {host}:/tmp/oc_bridge.py\n"
        f"ask the operator to run, in the hutch-python session:\n"
        f"    exec(open('/tmp/oc_bridge.py').read())"
    )


def ping(hutch: str, host: str | None = None) -> dict:
    """Is the live session reachable? Read-only, so never needs confirmation."""
    try:
        res = exec_code("True", hutch=hutch, host=host, timeout=T_QUERY)
    except (BridgeError, BridgeUnavailable) as exc:
        return {"ok": False, "detail": str(exc), "mode": "offline"}
    return {
        "ok": res["ok"] and res["output"].strip() == "True",
        "detail": res["output"].strip() or res.get("error"),
        "mode": "live",
        "host": res["host"],
    }


# --------------------------------------------------------------------------
# psdev (read /cds from S3DF)
# --------------------------------------------------------------------------
def psdev_python(script: str, params: dict[str, Any] | None = None, timeout: int = T_QUERY) -> str:
    """Run a stdlib-only Python script on psdev, with params injected as JSON.

    ``/sdf`` is not mounted on psdev, so the script must arrive on stdin and
    must not reference /sdf paths. Params are embedded as a JSON literal rather
    than string-formatted, so values cannot break out of the script.
    """
    prologue = f"import json\nP = json.loads({json.dumps(json.dumps(params or {}))})\n"
    return _ssh("psdev", "python3 -", stdin=(prologue + script).encode(), timeout=timeout, jump=None)


def psdev_read(path: str, timeout: int = T_QUERY) -> str:
    """cat a /cds file."""
    if path.startswith("/sdf"):
        raise BridgeError(f"/sdf is not mounted on psdev: {path}")
    return _ssh("psdev", f"cat {shlex.quote(path)}", timeout=timeout, jump=None)


# --------------------------------------------------------------------------
# controls machine (EPICS CA)
# --------------------------------------------------------------------------
CAGET = "/cds/group/pcds/epics/extensions/bin/linux-x86_64/caget"


def caget(pvs: list[str], *, hutch: str, timeout: int = T_QUERY) -> dict[str, str]:
    """Read PVs from a hutch control machine.

    CA is firewalled from both S3DF and psdev, so this must run on
    ``{hutch}-control``.
    """
    if not pvs:
        return {}
    host = f"{hutch.strip().lower()}-control"
    cmd = f"{shlex.quote(CAGET)} " + " ".join(shlex.quote(p) for p in pvs)
    out = _ssh(host, cmd, timeout=timeout)
    values: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            values[parts[0]] = parts[1].strip()
    missing = [p for p in pvs if p not in values]
    if missing:
        raise BridgeUnavailable(f"no value returned for {missing}")
    return values
