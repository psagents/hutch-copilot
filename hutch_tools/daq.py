"""hutch-daq: the DAQ machine -- bridge listener, process states, log files.

All three reach the DAQ machine over the same ssh path through ``psdev``.
Transport constraints (what is mounted where, why ``ssh -L`` cannot be used,
where EPICS CA reaches) are in README.md.

Everything sent to a live session passes ``require_confirmation`` first.
"""

from __future__ import annotations

import datetime
import json
import re
import shlex
import subprocess
import sys
from typing import Any

from .common import OK, REFUSED, UNREACHABLE, USAGE, fail, out, parser, script

# --------------------------------------------------------------------------
# Safety classification -- consulted before anything is transmitted
# --------------------------------------------------------------------------
READ_ONLY = "read-only"
WRITE = "write"
BEAM_CRITICAL = "beam-critical"

#: Increasing severity. A snippet is classified at its highest match.
SEVERITY = {READ_ONLY: 0, WRITE: 1, BEAM_CRITICAL: 2}

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------
# Positive evidence that a snippet only reads. Everything must match one of
# these for the snippet to be considered read-only.
_READ_ONLY_PATTERNS = [
    r"^\s*$",
    r"^\s*#",
    r"\.position\b",
    r"\.inserted\b",
    r"\.removed\b",
    r"\.get\(\s*\)",
    r"\.read\(\s*\)",
    r"\.describe\(\s*\)",
    r"\bdaq\.state\b",
    r"\bdaq\.status\(\s*\)",
    r"\bdaq\.config_info\(\s*\)",
    r"\bget_run\(\s*\)",
    r"\bcaget\b",
    r"\bcamonitor\s+-n\s*1\b",
    r"\bhappi\b",
    r"\blightpath\b",
    r"\bprint\(",
    r"^\s*(import|from)\s",
    r"^\s*(for|if|else|elif|try|except|finally|with|def|return|pass)\b",
    r"^\s*[A-Za-z_][\w.]*\s*$",  # bare attribute read, e.g. `daq.state`
    r"^\s*\w+\s*=\s*[^=]",  # local assignment (evaluated below on its RHS)
    r"\bTrue\b|\bFalse\b|\bNone\b",
]

# Operations that change the machine.
_WRITE_PATTERNS = {
    r"\.mv\(": "device move (.mv)",
    r"\.mvr\(": "relative device move (.mvr)",
    r"\.move\(": "device move (.move)",
    r"\.umv\(": "device move (.umv)",
    r"\.set\(": "setpoint write (.set)",
    r"\.put\(": "PV write (.put)",
    r"\bcaput\b": "PV write (caput)",
    r"\.insert\(": "device insert",
    r"\.remove\(": "device remove",
    r"\bdaq\.begin\(": "DAQ begin",
    r"\bdaq\.configure\(": "DAQ configure",
    r"\bdaq\.end_run\(": "DAQ end_run",
    r"\bdaq\.connect\(": "DAQ connect",
    r"\bdaq\.disconnect\(": "DAQ disconnect",
    r"\bautorun\(": "DATA run (autorun)",
    r"\bgeomrun\(": "GEOM run (geomrun)",
    r"\btakepeds\(": "DARK run (takepeds)",
    r"\bmakepeds\b": "pedestal deployment (makepeds)",
    r"\bRE\(": "Bluesky plan execution",
    r"\.stage\(": "device stage",
    r"\bopen\([^)]*['\"][wa]": "file write",
}

# Devices whose motion changes delivered beam. Matched on the whole snippet,
# so `mfx_att_1.set(...)` and `att1.mv(...)` both trip it.
#
# `\b` cannot be used: device names are underscore-joined and `_` is a word
# character, so `\bstopper\b` never matches `mfx_stopper_1`.
_TOK_START = r"(?<![A-Za-z0-9])"
_TOK_END = r"(?![A-Za-z0-9])"


def _tok(core: str) -> str:
    return _TOK_START + core + _TOK_END


_BEAM_CRITICAL_DEVICES = {
    _tok(r"stopper\w*"): "stopper",
    _tok(r"pps\w*"): "PPS stopper",
    _tok(r"mps\w*"): "MPS device",
    _tok(r"att(en\w*)?\d*"): "attenuator",
    _tok(r"apert\w*"): "aperture",
    _tok(r"shutter\w*"): "shutter",
    _tok(r"gatt\d*"): "gas attenuator",
    _tok(r"lodcm\w*"): "LODCM",
    _tok(r"mr\d+[a-z]\d+\w*"): "mirror (lightpath component)",
    _tok(r"picker\w*"): "pulse picker",
}


def _lines(code: str) -> list[str]:
    return [ln for ln in code.splitlines() if ln.strip()]


def classify(code: str) -> dict:
    """Classify a snippet. Returns {class, reasons, unmatched_lines}.

    Errs upward: any line that is not positively recognised as read-only makes
    the snippet at least WRITE.
    """
    reasons: list[str] = []
    verdict = READ_ONLY

    for pattern, label in _WRITE_PATTERNS.items():
        if re.search(pattern, code):
            reasons.append(label)
            verdict = WRITE

    if verdict == WRITE:
        for pattern, label in _BEAM_CRITICAL_DEVICES.items():
            if re.search(pattern, code, re.IGNORECASE):
                reasons.append(f"beam-critical target: {label}")
                verdict = BEAM_CRITICAL

    unmatched = [
        ln
        for ln in _lines(code)
        if not any(re.search(p, ln) for p in _READ_ONLY_PATTERNS)
    ]
    if verdict == READ_ONLY and unmatched:
        # Not provably read-only -> treat as a write and say which lines.
        verdict = WRITE
        reasons.append(
            "not provably read-only: "
            + "; ".join(ln.strip()[:60] for ln in unmatched[:3])
        )

    return {"class": verdict, "reasons": reasons, "unmatched_lines": unmatched}


class SafetyRefusal(PermissionError):
    """Raised when a snippet's class outranks the confirmation offered."""


def require_confirmation(code: str, confirmed_as: str | None) -> dict:
    """Gate a snippet. Raise unless ``confirmed_as`` matches its class or higher.

    ``confirmed_as`` is the class a human has actually approved. Passing a
    weaker class than the snippet requires is a refusal, not a downgrade --
    that is the whole point.
    """
    result = classify(code)
    needed = result["class"]
    if SEVERITY[needed] == 0:
        return result

    if confirmed_as is None:
        raise SafetyRefusal(
            f"this is a {needed} operation and no confirmation was supplied.\n"
            f"  why: {'; '.join(result['reasons'])}\n"
            f"  show the command to the operator, then re-send with "
            f"--confirm {needed}"
        )
    if confirmed_as not in SEVERITY:
        raise SafetyRefusal(f"unknown confirmation class {confirmed_as!r}")
    if SEVERITY[confirmed_as] < SEVERITY[needed]:
        raise SafetyRefusal(
            f"confirmed as {confirmed_as!r} but this is {needed!r}.\n"
            f"  why: {'; '.join(result['reasons'])}\n"
            f"  a {needed} operation needs an explicit {needed} confirmation, "
            f"including a beam-risk warning to the operator."
        )
    return result


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
#: SSH jump host for both /cds access and DAQ machines.
JUMP_HOST = "psdev"

#: Port oc_bridge.py listens on inside the hutch-python session.
BRIDGE_PORT = 9999

#: Timeout classes, in seconds. Named rather than magic numbers at call sites.
T_QUERY = 10
T_DEVICE = 30

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
    """The bridge could not be reached. Never fall back to a hardcoded default."""


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
    """DAQ machine for a hutch, e.g. mfx -> mfx-daq (the conventional spelling)."""
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

    ``confirm`` is the action class a human has approved. Read-only snippets
    need none; anything else is refused without a matching class.
    """
    verdict = require_confirmation(code, confirm)

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


def install_listener(hutch: str, host: str | None = None) -> str:
    """Copy oc_bridge.py to /tmp on the DAQ machine. Operator still starts it."""
    host = host or daq_host(hutch)
    src = script("scripts", "oc_bridge.py")
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
# DAQ processes and their logs
# 
# Neither `status` nor `logs` has behavioural coverage: both are remote-command
# wrappers whose correctness is in the output shape daqmgr actually produces,
# and there is no live DAQ machine to check that against yet.
# --------------------------------------------------------------------------
def process_status(hutch: str, host: str | None = None, timeout: int = T_DEVICE) -> dict:
    """``daqmgr status <hutch>.py`` on the DAQ machine: process name -> state."""
    hutch = hutch.strip().lower()
    host = host or daq_host(hutch)
    raw = _ssh(host, f"daqmgr status {shlex.quote(hutch + '.py')}", timeout=timeout)

    processes: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split()
        # daqmgr prints `<name> <state> ...`; anything else is a banner or a
        # blank. Keep the raw text so an unparsed format is still readable.
        if len(parts) >= 2 and not line.startswith(" "):
            processes[parts[0]] = parts[1]
    return {
        "host": host,
        "hutch": hutch,
        "processes": processes,
        "raw": raw,
    }


def _log_dir(hutch: str, when: "datetime.date | None" = None) -> str:
    """Per-process DAQ log directory, under /cds -- so it must be read remotely."""
    d = when or datetime.date.today()
    return f"/cds/home/opr/{hutch.strip().lower()}opr/{d.year:04d}/{d.month:02d}"


def logs(
    hutch: str,
    *,
    process: str | None = None,
    lines: int = 0,
    count: int = 20,
    host: str | None = None,
    when: "datetime.date | None" = None,
    timeout: int = T_DEVICE,
) -> dict:
    """List the most recent DAQ log files, or tail the newest when ``lines`` > 0.

    Files are named ``<timestamp>_<host>:<process>.log``.
    """
    hutch = hutch.strip().lower()
    host = host or daq_host(hutch)
    d = _log_dir(hutch, when)
    # A process filter matches the `:<process>.log` field, not the whole name,
    # so it cannot accidentally match the host or the timestamp.
    pattern = f"*:{process}.log" if process else "*.log"
    listing = (
        f"ls -1t {shlex.quote(d)}/{pattern} 2>/dev/null | head -n {int(count)}"
    )

    if lines > 0:
        newest = f"$({listing} | head -n 1)"
        raw = _ssh(
            host,
            f'f={newest}; [ -n "$f" ] && echo "==> $f" && tail -n {int(lines)} "$f"',
            timeout=timeout,
        )
        return {"host": host, "directory": d, "process": process,
                "tail_lines": lines, "content": raw}

    raw = _ssh(host, listing, timeout=timeout)
    files = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return {
        "host": host,
        "directory": d,
        "process": process,
        "count": len(files),
        "files": files,
    }


# --------------------------------------------------------------------------
# psdev (read /cds from S3DF)
# --------------------------------------------------------------------------
def psdev_python(script: str, params: dict[str, Any] | None = None, timeout: int = T_QUERY) -> str:
    """Run a stdlib-only Python script on psdev, params injected as a JSON literal.

    Arrives on stdin and must not name /sdf paths -- /sdf is not mounted there.
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
    """Read PVs from ``{hutch}-control`` -- the only host where CA reaches the IOCs."""
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


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
#: Command help, shared by build_parser() and main().
_DOC = """hutch-daq: ping | install | exec | status | logs."""


def _confirm_and_timeout(p, post: bool = False) -> None:
    """Add --confirm/--timeout. ``post=True`` uses shadow dests so the subparser
    cannot clobber a value given before the subcommand; ``_resolve`` picks."""
    suffix = "_post" if post else ""
    p.add_argument("--confirm", dest="confirm" + suffix,
                   choices=[READ_ONLY, WRITE, BEAM_CRITICAL], default=None,
                   help="action class a human has approved")
    p.add_argument("--timeout", dest="timeout" + suffix, type=int,
                   default=None if post else T_QUERY)


def _resolve(args) -> tuple:
    """(confirm, timeout), taking the post-subcommand spelling when both appear."""
    confirm = getattr(args, "confirm_post", None) or args.confirm
    timeout = getattr(args, "timeout_post", None) or args.timeout
    return confirm, timeout


def build_parser() -> "argparse.ArgumentParser":
    """Construct this command's parser (kept separate so it can be introspected).

    ``--confirm`` and ``--timeout`` are accepted on both sides of the
    subcommand; the trailing one wins.
    """

    ap, sub = parser("hutch-daq", _DOC)
    ap.add_argument("--hutch", required=True)
    ap.add_argument("--host", default=None, help="override the DAQ host")
    _confirm_and_timeout(ap)

    sub.add_parser("ping")
    sub.add_parser("install", help="copy oc_bridge.py to the DAQ machine")

    exec_parser = sub.add_parser("exec", help="run python in the live hutch-python session")
    exec_parser.add_argument("code", help="python source, or - to read stdin")
    # A distinct dest is required: argparse resolves the subparser into the same
    # namespace, so a same-dest option would reset the parent's value.
    _confirm_and_timeout(exec_parser, post=True)

    sub.add_parser("status", help="daqmgr process states on the DAQ machine")

    logs_parser = sub.add_parser("logs", help="list or tail per-process DAQ log files")
    logs_parser.add_argument("--process", default=None,
                             help="filter the :<process>.log field, e.g. drp, teb, control")
    logs_parser.add_argument("-n", "--lines", type=int, default=0,
                             help="tail this many lines of the newest match; 0 lists instead")
    logs_parser.add_argument("--count", type=int, default=20, help="how many names to list")

    return ap


def main(argv: "list[str] | None" = None) -> int:
    """hutch-daq: ping | install | exec | status | logs."""
    args = build_parser().parse_args(argv)
    confirm, timeout = _resolve(args)
    try:
        if args.cmd == "ping":
            res = ping(args.hutch, host=args.host)
            out(res)
            return OK if res["ok"] else UNREACHABLE
        if args.cmd == "install":
            return out(install_listener(args.hutch, host=args.host), plain=True)
        if args.cmd == "status":
            return out(process_status(args.hutch, host=args.host, timeout=timeout))
        if args.cmd == "logs":
            return out(logs(args.hutch, process=args.process, lines=args.lines,
                            count=args.count, host=args.host, timeout=timeout))
        if args.cmd == "exec":
            code = sys.stdin.read() if args.code == "-" else args.code
            verdict = classify(code)
            if confirm is None and SEVERITY[verdict["class"]] > 0:
                # Nothing is transmitted: exec_code is never reached.
                out(verdict)
                return REFUSED
            res = exec_code(code, hutch=args.hutch, confirm=confirm,
                            timeout=timeout, host=args.host)
            out(res)
            return OK if res["ok"] else UNREACHABLE
    except SafetyRefusal as exc:
        return fail(exc, REFUSED)
    except BridgeError as exc:
        return fail(exc, UNREACHABLE)
    return USAGE
